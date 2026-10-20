"""Run transactions and model admission against PostgreSQL, without external inference."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from uuid import uuid4

import pytest
from agents import ModelSettings
from agents.models.interface import ModelTracing
from agents.usage import InputTokensDetails, Usage
from sqlalchemy import func, select

from opsmesh.bootstrap.orchestration import execution_bindings
from opsmesh.bootstrap.routers import ActiveRunActivity, ConfigurationExecutionScopeAdmission
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.quotas import service as quotas
from opsmesh.identity.quotas.models import TokenReservation, UserTokenAccount
from opsmesh.identity.quotas.schemas import NormalizedUsage
from opsmesh.identity.users.service import create_user
from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject
from opsmesh.orchestration.execution.contracts import (
    ExecutionRequest,
    ModelConnection,
    RunReference,
    UncertainOperationError,
)
from opsmesh.orchestration.runs import reconciliation, service
from opsmesh.orchestration.runs.activities import RunActivities
from opsmesh.orchestration.runs.contracts import RunCompletion
from opsmesh.orchestration.runs.model_activities import ExecutionModelProvider, ModelActivities
from opsmesh.orchestration.runs.models import OperationReceipt, Run
from opsmesh.orchestration.runs.schemas import ReconcileRequest
from opsmesh.orchestration.runs.usage import normalize_usage
from opsmesh.orchestration.sessions.contracts import HistoryOperation
from opsmesh.orchestration.sessions.history import HistoryGateway
from opsmesh.orchestration.sessions.models import SessionRecord
from opsmesh.orchestration.sessions.service import create_session, operate_history
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.errors import DomainError
from opsmesh.shared.security.encryption import SecretCipher

KEY = "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0="


class Connections:
    def resolve(self, session, *, subject, scope, connection_id, model):
        return ModelConnection("local-test-key", "https://api.openai.com/v1", 1000)


@dataclass
class Harness:
    sessions: object
    principal: CurrentUser
    request: ExecutionRequest
    runs: RunActivities

    def submit(self, key="run-one", request=None):
        with self.runs.sessions() as session, session.begin():
            return service.execute(
                session,
                subject=ExecutionSubject(self.principal.user_id),
                admission=self.runs.admission,
                cipher=self.runs.cipher,
                request=request or self.request,
                idempotency_key=key,
            )

    def claim(self, run):
        return self.runs._prepare(
            RunReference(execution_id=run.id, run_id=run.id, workflow_id=f"run:{run.id}")
        )

    def model(self, run):
        provider = ExecutionModelProvider(
            self.runs.sessions,
            self.runs.bindings,
            self.runs.admission,
            Connections(),
            self.runs.cipher,
        )
        return ModelActivities(provider, run.id, "main")


@pytest.fixture
def harness(database_sessions):
    cipher = SecretCipher(key=KEY, key_id="test")
    admission = ConfigurationExecutionScopeAdmission()
    with database_sessions() as session, session.begin():
        user = create_user(
            session,
            email="s7-run@example.com",
            display_name="Run test",
            platform_roles=frozenset({"admin"}),
        )
        quotas.ensure_ledger(session, user_id=user.id, token_limit=10_000)
        quotas.ensure_capacity_accounts(
            session, user_id=user.id, limits={"concurrency": 8, "environment": 4}
        )
        model_session = create_session(
            session, subject=ExecutionSubject(user.id), scope=ExecutionScope(), admission=admission
        )
    principal = CurrentUser(user.id, user.email, user.display_name, "active", frozenset({"admin"}))
    request = ExecutionRequest.model_validate(
        {
            "session_id": str(model_session.id),
            "input": "hello",
            "entry_agent": "main",
            "agents": {
                "main": {
                    "name": "Main",
                    "instructions": "Answer",
                    "model": {
                        "connection_id": str(uuid4()),
                        "name": "model-test",
                        "max_output_tokens": 100,
                    },
                }
            },
        }
    )
    return Harness(
        database_sessions,
        principal,
        request,
        RunActivities(database_sessions, admission, execution_bindings(), cipher),
    )


def model_claim(model, operation_id="model-operation"):
    return model.claim(
        (None, "hello", ModelSettings(max_tokens=100), [], None, [], ModelTracing.DISABLED),
        {"previous_response_id": None, "conversation_id": None, "prompt": None},
        operation_id,
        False,
    )


def test_submission_replays_and_does_not_take_a_slot(harness):
    first = harness.submit()
    repeated = harness.submit()
    assert first.id == repeated.id
    assert first.status == "queued" and first.capacity_reservation_id is None
    with harness.runs.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 1
        assert session.scalar(select(func.count()).select_from(TokenReservation)) == 0


def test_same_session_runs_are_fifo_and_approval_wait_retains_session_head(harness):
    first, second = harness.submit(), harness.submit("two")
    assert harness.claim(second) is None
    assert harness.claim(first) is not None
    assert harness.claim(second) is None
    with harness.runs.sessions() as session, session.begin():
        session.get(Run, first.id).status = "waiting_approval"
    assert harness.claim(second) is None
    harness.runs._finish(RunCompletion(run_id=first.id, status="cancelled"))
    assert harness.claim(second) is not None


def test_different_users_can_execute_their_own_sessions_concurrently(harness):
    first = harness.submit()
    assert harness.claim(first) is not None
    with harness.runs.sessions() as session, session.begin():
        other = create_user(session, email="parallel-user@example.com", display_name="Other")
        quotas.ensure_ledger(session, user_id=other.id, token_limit=10_000)
        quotas.ensure_capacity_accounts(
            session, user_id=other.id, limits={"concurrency": 1, "environment": 1}
        )
        personal = create_session(
            session,
            subject=ExecutionSubject(other.id),
            scope=ExecutionScope(),
            admission=harness.runs.admission,
        )
        second = service.execute(
            session,
            subject=ExecutionSubject(other.id),
            admission=harness.runs.admission,
            cipher=harness.runs.cipher,
            request=harness.request.model_copy(update={"session_id": personal.id}),
            idempotency_key="other-user",
        )
    assert harness.claim(second) is not None
    with harness.runs.sessions() as session:
        assert session.get(Run, first.id).status == session.get(Run, second.id).status == "running"


def test_reset_refuses_queued_task_without_cancelling_it(harness):
    queued = harness.submit()
    with pytest.raises(DomainError) as refused:
        asyncio.run(
            operate_history(
                harness.runs.sessions,
                HistoryGateway(harness.runs.sessions),
                subject=ExecutionSubject(harness.principal.user_id),
                admission=harness.runs.admission,
                activity=ActiveRunActivity(),
                session_id=harness.request.session_id,
                operation=HistoryOperation(method="clear_session"),
                operation_id="reset",
            )
        )
    assert refused.value.code == "session_has_active_runs"
    with harness.runs.sessions() as session:
        assert session.get(Run, queued.id).status == "queued"


def test_token_refusal_rolls_back_hold_and_operation_intent(harness):
    run = harness.submit()
    harness.claim(run)
    with harness.runs.sessions() as session, session.begin():
        quotas.set_limits(
            session,
            user_id=harness.principal.user_id,
            actor=AuditActor.from_user(harness.principal),
            token_limit=1000,
        )
    with pytest.raises(DomainError):
        model_claim(harness.model(run))
    with harness.runs.sessions() as session:
        account = session.get(UserTokenAccount, harness.principal.user_id)
        assert account.reserved_tokens == 0
        assert session.scalar(select(func.count()).select_from(OperationReceipt)) == 0
        assert session.scalar(select(func.count()).select_from(TokenReservation)) == 0


def test_model_hold_settlement_and_replay_are_atomic(harness):
    run = harness.submit()
    harness.claim(run)
    model = harness.model(run)
    assert model_claim(model)[0]
    with harness.runs.sessions() as session:
        assert session.get(UserTokenAccount, harness.principal.user_id).reserved_tokens == 1100
        assert session.scalar(select(TokenReservation)).status == "reserved"
    model.complete(
        "model-operation",
        {"confirmed": True},
        NormalizedUsage(input_tokens=80, cached_input_tokens=20, output_tokens=10),
    )
    assert model_claim(model)[:2] == (False, {"confirmed": True})
    with harness.runs.sessions() as session:
        account = session.get(UserTokenAccount, harness.principal.user_id)
        assert (account.used_tokens, account.reserved_tokens) == (110, 0)
        assert session.scalar(select(TokenReservation)).status == "settled"


def test_concurrent_same_operation_never_grants_a_second_effect(harness):
    run = harness.submit()
    harness.claim(run)

    def claim():
        try:
            return model_claim(harness.model(run))[0]
        except UncertainOperationError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: claim(), range(2))) == [False, True]
    with harness.runs.sessions() as session:
        assert session.scalar(select(func.count()).select_from(TokenReservation)) == 1


def test_unknown_model_retains_quota_and_admin_reconciliation_never_repeats_it(harness):
    run = harness.submit()
    harness.claim(run)
    queued = harness.submit("queued-behind-unknown")
    model = harness.model(run)
    model_claim(model)
    model.complete("model-operation", None, None)
    harness.runs._finish(RunCompletion(run_id=run.id, status="failed"))
    with pytest.raises(UncertainOperationError):
        model_claim(model)
    with harness.runs.sessions() as session:
        current = session.get(Run, run.id)
        assert current.status == "needs_attention"
        assert session.get(UserTokenAccount, harness.principal.user_id).reserved_tokens == 1100
        revision = current.event_sequence
    payload = ReconcileRequest(
        command_id=uuid4(),
        expected_revision=revision,
        evidence={"provider_receipt": "not-executed-1"},
        operations=[{"operation_id": "model-operation", "outcome": "not_executed"}],
    )
    for _ in range(2):
        with harness.runs.sessions() as session, session.begin():
            result = reconciliation.reconcile(
                session,
                run_id=run.id,
                actor=harness.principal,
                request=payload,
                cipher=harness.runs.cipher,
                bindings=harness.runs.bindings,
            )
            assert result.status == "failed"
    with harness.runs.sessions() as session:
        assert session.get(UserTokenAccount, harness.principal.user_id).reserved_tokens == 0
        assert session.scalar(select(TokenReservation)).status == "released"
        assert session.get(SessionRecord, run.session_id).status == "closing"
        assert session.get(Run, queued.id).status == "cancelling"
    assert harness.claim(queued) is None
    harness.runs._finish(RunCompletion(run_id=queued.id, status="cancelled"))
    with harness.runs.sessions() as session:
        assert session.get(SessionRecord, run.session_id).status == "closed"
    with pytest.raises(DomainError):
        harness.submit("cannot-reuse-reconciled-session")


def test_cached_tokens_are_counted_separately_without_double_counting():
    normalized = normalize_usage(
        Usage(
            input_tokens=100,
            output_tokens=10,
            input_tokens_details=InputTokensDetails(cached_tokens=20, cache_write_tokens=0),
        )
    )
    assert normalized == NormalizedUsage(input_tokens=80, output_tokens=10, cached_input_tokens=20)
