"""Neutral execution contracts: subjects, admission, ledgers and effects.

Execution owns runs, native model sessions and environments only. Every
cross-owner need — the verified subject, workspace scope admission, the
three ledgers (user tokens, workspace capacity, budget), run assets and
operation reconciliation — enters through the values and protocols
declared here and is assembled by bootstrap. Ledger calls run inside the
caller's session under the documented lock order (Session/Run → user
tokens → workspace capacity → budget) and never commit on their own; only
serializable values such as operation ids and reservation ids may cross a
Temporal Workflow boundary, never a live ledger row or session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor
from opsmesh.governance.costs.schemas import NormalizedUsage
from opsmesh.shared.errors import ForbiddenError
from opsmesh.shared.files import validate_relative_path


@dataclass(frozen=True)
class ExecutionSubject:
    """The verified principal of one execution action.

    Platform roles stay in identity and space roles stay with the space
    owner; this value carries only the principal the ledgers and the
    admission charge. Routes build it from the authenticated user.
    """

    user_id: UUID


@dataclass(frozen=True)
class ExecutionScope:
    """One external scope an execution action may name.

    A workspace or project scope is admitted by the injected owner; the
    user scope needs no external owner because the session, run and
    environment records are authorized by their own owner checks.
    """

    workspace_id: UUID | None = None
    project_id: UUID | None = None

    @property
    def is_user_scope(self) -> bool:
        return self.workspace_id is None and self.project_id is None


@runtime_checkable
class ScopeAdmission(Protocol):
    """Whether one subject may act inside one external scope.

    The admission runs inside the caller's session, exactly like the
    ledgers: the real owner checks membership in the same transaction
    that writes the execution fact.
    """

    def admit(self, session: Session, *, subject: ExecutionSubject, scope: ExecutionScope) -> None:
        """Raise a domain error when the scope is not admitted for the subject."""
        ...


class RejectingScopeAdmission:
    """The default admission: the user scope passes, every external scope is refused.

    Bootstrap replaces this with the real workspace admission; until then a
    request that names a workspace or project scope fails closed instead of
    being treated as user scope.
    """

    def admit(self, session: Session, *, subject: ExecutionSubject, scope: ExecutionScope) -> None:
        if scope.is_user_scope:
            return
        _ = session, subject
        raise ForbiddenError(
            "The execution scope is not admitted",
            details={
                "workspace_id": str(scope.workspace_id) if scope.workspace_id else None,
                "project_id": str(scope.project_id) if scope.project_id else None,
            },
        )


class ExecutionContext(BaseModel):
    """The neutral context of one execution action.

    It carries the verified principal, the scope value and the current
    operation id. It is built by the caller (route or activity) and never
    reconstructed from persisted or external input.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: UUID
    principal_id: UUID
    owner_ref: str = Field(min_length=1, max_length=200)
    run_id: UUID | None = None
    session_id: UUID | None = None
    operation_id: str | None = Field(default=None, max_length=240)


@runtime_checkable
class ReservationRef(Protocol):
    """The read-only handle of one held ledger reservation.

    A ledger returns its own reservation row through this handle; only its
    id is serializable, so a workflow carries the id and the activity
    resolves the row again inside its own session.
    """

    @property
    def id(self) -> UUID: ...


@runtime_checkable
class TokenLedger(Protocol):
    """The identity-owned user token ledger, called inside the caller's session."""

    def reserve_tokens(
        self,
        session: Session,
        *,
        user_id: UUID,
        amount: int,
        idempotency_scope: str,
        idempotency_key: str,
    ) -> ReservationRef:
        """Hold one token upper bound and return its reservation handle."""
        ...

    def settle_tokens(
        self, session: Session, *, reservation_id: UUID, actual_amount: int
    ) -> None:
        """Settle one reservation with its actual token consumption."""
        ...

    def release_tokens(self, session: Session, *, reservation_id: UUID) -> None:
        """Release one reservation without recording consumption."""
        ...


@runtime_checkable
class CapacityLedger(Protocol):
    """The workspace-owned capacity ledger, called inside the caller's session."""

    def reserve(
        self,
        session: Session,
        *,
        workspace_id: UUID,
        kind: str,
        amount: int,
        idempotency_scope: str,
        idempotency_key: str,
    ) -> ReservationRef:
        """Hold one amount of one capacity kind and return its handle."""
        ...

    def release(self, session: Session, *, workspace_id: UUID, reservation_id: UUID) -> None:
        """Release one held reservation."""
        ...


@runtime_checkable
class CostLedger(Protocol):
    """The governance-owned budget ledger, called inside the caller's session."""

    def reserve_budget(
        self,
        session: Session,
        *,
        actor: AuditActor,
        operation_id: str,
        workspace_id: UUID | None,
        currency: str,
        subject_kind: str,
        subject_key: str,
        usage_upper_bound: NormalizedUsage,
        run_ref: str | None = None,
    ) -> ReservationRef:
        """Hold one operation's verifiable cost upper bound under a pinned price sheet."""
        ...

    def settle_budget(
        self,
        session: Session,
        *,
        actor: AuditActor,
        operation_id: str,
        actual_usage: NormalizedUsage,
    ) -> None:
        """Settle one reservation with its actual usage and free the remainder."""
        ...

    def release_budget(
        self, session: Session, *, actor: AuditActor, operation_id: str, reason: str
    ) -> None:
        """Release one held reservation whose execution provably never ran."""
        ...


@dataclass(frozen=True)
class ExecutionBindings:
    """The three ledgers one execution action admits against.

    All three are required: a missing member fails assembly (or blocks the
    run) instead of silently admitting work against a ledger nobody owns.
    They are runtime assembly objects and never enter a workflow parameter
    or history.
    """

    tokens: TokenLedger
    capacity: CapacityLedger
    budget: CostLedger


@dataclass(frozen=True)
class AdmissionTicket:
    """What one admission held, as serializable references.

    A run persists this ticket when its capacity, tokens and budget are
    reserved; settlement and release resolve the same references later.
    """

    operation_id: str
    token_reservation_id: UUID | None = None
    capacity_reservation_id: UUID | None = None
    budget_operation_id: str | None = None


class AssetRef(BaseModel):
    """One content-addressed input asset of a run.

    The destination is a safe relative path inside the execution
    environment: the shared path validator rejects absolute paths, parent
    segments and backslashes, and the transfer resolves containment
    against the environment root before writing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: str = Field(min_length=1, max_length=240)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    destination: str = Field(min_length=1, max_length=240)

    @field_validator("destination")
    @classmethod
    def safe_destination(cls, value: str) -> str:
        return validate_relative_path(value)


class OperationResolution(BaseModel):
    """The confirmed outcome of one external operation.

    Reconciliation applies a resolution to an operation whose effect was
    uncertain; usage is non-negative because a confirmed outcome never
    un-consumes tokens.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, max_length=240)
    result: Any = None
    usage: dict[str, int] | None = None
    storage_bytes: int | None = Field(default=None, ge=0)

    @field_validator("usage")
    @classmethod
    def non_negative_usage(cls, value: dict[str, int] | None) -> dict[str, int] | None:
        if value is not None and any(amount < 0 for amount in value.values()):
            raise ValueError("Confirmed usage must be non-negative")
        return value


class UncertainOperationError(RuntimeError):
    """An external operation may have succeeded and must not be repeated blindly."""
