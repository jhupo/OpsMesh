"""Execution contract tests without a database.

These cover the neutral contracts every later execution step assembles
against: the bindings require all three ledgers, the injected scope
admission fails closed, the run asset destination is a safe relative
path, and each ledger protocol mirrors the real owner service signature
so an adapter can delegate without translation. The check constraint
literals are compared against the model constants so a status added to a
model cannot drift away from its database constraint.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

from opsmesh.execution.contracts import (
    AdmissionTicket,
    AssetRef,
    CapacityLedger,
    CostLedger,
    ExecutionBindings,
    ExecutionContext,
    ExecutionScope,
    ExecutionSubject,
    OperationResolution,
    RejectingScopeAdmission,
    ReservationRef,
    ScopeAdmission,
    TokenLedger,
    UncertainOperationError,
)
from opsmesh.execution.runs.models import (
    RECEIPT_KINDS,
    RUN_STATUSES,
    OperationReceipt,
    Run,
)
from opsmesh.execution.runtime.models import (
    ENVIRONMENT_ACTIONS,
    ENVIRONMENT_STATUSES,
    MCP_ACTIONS,
    MCP_CONNECTION_STATUSES,
    MCP_TRANSPORTS,
    RECEIPT_STATUSES,
    Environment,
    EnvironmentOperationReceipt,
    MCPConnection,
    MCPOperationReceipt,
)
from opsmesh.execution.sessions.models import (
    SESSION_METHODS,
    SESSION_STATUSES,
    SessionOperationReceipt,
    SessionRecord,
)
from opsmesh.governance.costs import service as costs
from opsmesh.governance.costs.schemas import NormalizedUsage
from opsmesh.identity.quotas import service as quotas
from opsmesh.shared.db.base import Base
from opsmesh.shared.errors import ForbiddenError
from opsmesh.workspaces.capacity import service as capacity

DIGEST = "a" * 64


class StubTokens:
    """The minimal shape the bindings need; behaviour is not under test."""

    def reserve_tokens(
        self,
        session: object,
        *,
        user_id: UUID,
        amount: int,
        idempotency_scope: str,
        idempotency_key: str,
    ) -> object:
        raise NotImplementedError

    def settle_tokens(
        self, session: object, *, reservation_id: UUID, actual_amount: int
    ) -> None:
        raise NotImplementedError

    def release_tokens(self, session: object, *, reservation_id: UUID) -> None:
        raise NotImplementedError


class StubCapacity:
    def reserve(
        self,
        session: object,
        *,
        workspace_id: UUID,
        kind: str,
        amount: int,
        idempotency_scope: str,
        idempotency_key: str,
    ) -> object:
        raise NotImplementedError

    def release(self, session: object, *, workspace_id: UUID, reservation_id: UUID) -> None:
        raise NotImplementedError


class StubBudget:
    def reserve_budget(
        self,
        session: object,
        *,
        actor: object,
        operation_id: str,
        workspace_id: UUID | None,
        currency: str,
        subject_kind: str,
        subject_key: str,
        usage_upper_bound: NormalizedUsage,
        run_ref: str | None = None,
    ) -> object:
        raise NotImplementedError

    def settle_budget(
        self,
        session: object,
        *,
        actor: object,
        operation_id: str,
        actual_usage: NormalizedUsage,
    ) -> None:
        raise NotImplementedError

    def release_budget(
        self, session: object, *, actor: object, operation_id: str, reason: str
    ) -> None:
        raise NotImplementedError


def bindings() -> ExecutionBindings:
    return ExecutionBindings(
        tokens=StubTokens(), capacity=StubCapacity(), budget=StubBudget()
    )


class TestExecutionBindings:
    def test_every_ledger_is_required(self) -> None:
        with pytest.raises(TypeError):
            ExecutionBindings()  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            ExecutionBindings(tokens=StubTokens(), capacity=StubCapacity())  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            ExecutionBindings(tokens=StubTokens(), budget=StubBudget())  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            ExecutionBindings(capacity=StubCapacity(), budget=StubBudget())  # type: ignore[call-arg]

    def test_the_bindings_are_frozen(self) -> None:
        assembled = bindings()
        with pytest.raises(AttributeError):
            assembled.tokens = StubTokens()  # type: ignore[misc]

    def test_the_stubs_satisfy_the_ledger_protocols(self) -> None:
        assembled = bindings()
        assert isinstance(assembled.tokens, TokenLedger)
        assert isinstance(assembled.capacity, CapacityLedger)
        assert isinstance(assembled.budget, CostLedger)


class TestReservationRef:
    def test_a_reservation_handle_satisfies_the_reference(self) -> None:
        @dataclass(frozen=True)
        class Handle:
            id: UUID

        assert isinstance(Handle(id=uuid4()), ReservationRef)


class TestScopeAdmission:
    def test_the_default_admission_fails_closed_outside_the_user_scope(self) -> None:
        admission = RejectingScopeAdmission()
        subject = ExecutionSubject(user_id=UUID(int=7))
        admission.admit(None, subject=subject, scope=ExecutionScope())  # type: ignore[arg-type]
        with pytest.raises(ForbiddenError):
            admission.admit(
                None,  # type: ignore[arg-type]
                subject=subject,
                scope=ExecutionScope(workspace_id=UUID(int=9)),
            )

    def test_the_default_admission_satisfies_the_protocol(self) -> None:
        assert isinstance(RejectingScopeAdmission(), ScopeAdmission)

    def test_the_scope_reports_the_user_scope(self) -> None:
        assert ExecutionScope().is_user_scope is True
        assert ExecutionScope(workspace_id=UUID(int=3)).is_user_scope is False
        assert ExecutionScope(project_id=UUID(int=4)).is_user_scope is False


class TestExecutionContext:
    def test_the_context_defaults_to_no_operation(self) -> None:
        context = ExecutionContext(
            scope_id=UUID(int=1), principal_id=UUID(int=2), owner_ref="workspace:1"
        )
        assert context.operation_id is None
        assert context.run_id is None
        assert context.session_id is None

    def test_the_context_is_frozen_and_forbids_extras(self) -> None:
        context = ExecutionContext(
            scope_id=UUID(int=1), principal_id=UUID(int=2), owner_ref="workspace:1"
        )
        with pytest.raises(ValidationError):
            context.operation_id = "op"  # type: ignore[misc]
        with pytest.raises(ValidationError):
            ExecutionContext(  # type: ignore[call-arg]
                scope_id=UUID(int=1),
                principal_id=UUID(int=2),
                owner_ref="workspace:1",
                unexpected="value",
            )


class TestAssetRef:
    def test_a_safe_relative_destination_is_accepted(self) -> None:
        asset = AssetRef(ref="file-1", digest=DIGEST, destination="work/inputs")
        assert asset.destination == "work/inputs"

    @pytest.mark.parametrize(
        "destination",
        ["/absolute", "../escape", "a//b", "a\\b", "", ".", "nested/../../escape"],
    )
    def test_an_unsafe_destination_is_refused(self, destination: str) -> None:
        with pytest.raises(ValidationError):
            AssetRef(ref="file-1", digest=DIGEST, destination=destination)

    def test_the_digest_must_be_a_hex_sha256(self) -> None:
        with pytest.raises(ValidationError):
            AssetRef(ref="file-1", digest="not-a-digest", destination="work")

    def test_unknown_fields_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            AssetRef(ref="file-1", digest=DIGEST, destination="work", extra="value")  # type: ignore[call-arg]


class TestOperationResolution:
    def test_a_confirmed_outcome_keeps_its_usage(self) -> None:
        resolution = OperationResolution(
            operation_id="op-1", result={"ok": True}, usage={"input_tokens": 10}
        )
        assert resolution.usage == {"input_tokens": 10}
        assert resolution.storage_bytes is None

    def test_negative_usage_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            OperationResolution(operation_id="op-1", usage={"input_tokens": -1})

    def test_negative_storage_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            OperationResolution(operation_id="op-1", storage_bytes=-1)


class TestUncertainOperationError:
    def test_an_uncertain_operation_is_a_runtime_error(self) -> None:
        error = UncertainOperationError("unknown effect")
        assert isinstance(error, RuntimeError)


class TestAdmissionTicket:
    def test_the_ticket_defaults_to_no_holds(self) -> None:
        ticket = AdmissionTicket(operation_id="op-1")
        assert ticket.token_reservation_id is None
        assert ticket.capacity_reservation_id is None
        assert ticket.budget_operation_id is None


LEDGER_MIRRORS: tuple[tuple[Callable[..., object], Callable[..., object]], ...] = (
    (TokenLedger.reserve_tokens, quotas.reserve_tokens),
    (TokenLedger.settle_tokens, quotas.settle_tokens),
    (TokenLedger.release_tokens, quotas.release_tokens),
    (CapacityLedger.reserve, capacity.reserve),
    (CapacityLedger.release, capacity.release),
    (CostLedger.reserve_budget, costs.reserve_budget),
    (CostLedger.settle_budget, costs.settle_budget),
    (CostLedger.release_budget, costs.release_budget),
)


def _parameters(function: Callable[..., object]) -> dict[str, inspect.Parameter]:
    return dict(inspect.signature(function, eval_str=True).parameters)


class TestLedgerProtocolMirrors:
    @pytest.mark.parametrize(("protocol_method", "service_function"), LEDGER_MIRRORS)
    def test_the_protocol_mirrors_the_real_service_signature(
        self, protocol_method: Callable[..., object], service_function: Callable[..., object]
    ) -> None:
        protocol_parameters = _parameters(protocol_method)
        protocol_parameters.pop("self")
        service_parameters = _parameters(service_function)
        assert list(protocol_parameters) == list(service_parameters)
        for name, expected in service_parameters.items():
            actual = protocol_parameters[name]
            assert actual.kind is expected.kind, name
            assert actual.annotation == expected.annotation, name
            assert actual.default is expected.default, name


def _check_literals(model: type[Base], constraint_name: str) -> set[str]:
    # The naming convention prefixes a named check with ck_<table>_.
    for constraint in model.__table__.constraints:
        if isinstance(constraint, CheckConstraint) and (constraint.name or "").endswith(
            constraint_name
        ):
            return set(re.findall(r"'([^']*)'", str(constraint.sqltext)))
    raise AssertionError(f"{model.__name__} has no check constraint named {constraint_name}")


CHECK_LITERALS: tuple[tuple[type[Base], str, tuple[str, ...]], ...] = (
    (Run, "run_status", RUN_STATUSES),
    (OperationReceipt, "run_receipt_kind", RECEIPT_KINDS),
    (SessionRecord, "valid_status", SESSION_STATUSES),
    (SessionOperationReceipt, "session_receipt_method", SESSION_METHODS),
    (Environment, "valid_status", ENVIRONMENT_STATUSES),
    (MCPConnection, "mcp_connection_transport", MCP_TRANSPORTS),
    (MCPConnection, "valid_status", MCP_CONNECTION_STATUSES),
    (EnvironmentOperationReceipt, "environment_receipt_action", ENVIRONMENT_ACTIONS),
    (EnvironmentOperationReceipt, "valid_status", RECEIPT_STATUSES),
    (MCPOperationReceipt, "mcp_receipt_action", MCP_ACTIONS),
    (MCPOperationReceipt, "valid_status", RECEIPT_STATUSES),
)


class TestCheckConstraints:
    @pytest.mark.parametrize(("model", "constraint_name", "statuses"), CHECK_LITERALS)
    def test_the_constraint_literals_match_the_model_constants(
        self, model: type[Base], constraint_name: str, statuses: tuple[str, ...]
    ) -> None:
        literals = _check_literals(model, constraint_name)
        assert literals == set(statuses)
