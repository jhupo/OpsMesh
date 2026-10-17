"""S6.5 governance cost tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must run against a real PostgreSQL instance (never SQLite). They cover the
acceptance surface of V4 section 16.2 for the cost ledger: the three budget
configuration states, the pinned price sheet and the reservation state machine
(reserve / settle / release / uncertain / reconcile), the release matrix, the
window roll with cross-window attribution, budget revisions, the composition
of the three ledgers inside one caller transaction, and the forced-window
serialization of concurrent reservations.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.audit.service import AuditActor
from opsmesh.governance.costs import service as costs
from opsmesh.governance.costs.models import (
    BUDGET_PERIOD_MONTHLY,
    BUDGET_PERIOD_NONE,
    RECONCILE_FAILED,
    RECONCILE_NOT_EXECUTED,
    RECONCILE_SUCCEEDED,
    CostBudget,
    CostBudgetReservation,
    CostBudgetWindow,
    CostPricingRuleVersion,
    CostUsageRecord,
)
from opsmesh.governance.costs.pricing import quote_max_cost, unit_prices_of_version
from opsmesh.governance.costs.schemas import NormalizedUsage, UnitPrices
from opsmesh.identity.quotas import service as quotas
from opsmesh.identity.quotas.models import TokenReservation, UserTokenAccount
from opsmesh.identity.users import service as users
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.secrets import SecretCipher
from opsmesh.workspaces.capacity import service as capacity
from opsmesh.workspaces.capacity.models import (
    CAPACITY_CONCURRENCY,
    WorkspaceCapacityReservation,
)
from opsmesh.workspaces.models import Workspace

PLATFORM_ADMIN_ID = UUID("aaaaaaaa-1111-2222-3333-444444444444")
WORKSPACE_OWNER_ID = UUID("bbbbbbbb-1111-2222-3333-444444444444")
WORKSPACE_ID = UUID("cccccccc-1111-2222-3333-444444444444")
CURRENCY = "USD"
MODEL_SUBJECT = "gpt-test-model"
TOOL_SUBJECT = "web-search"

MODEL_PRICES = UnitPrices(
    currency=CURRENCY,
    input_price=Decimal("0.001"),
    output_price=Decimal("0.002"),
    cached_input_price=Decimal("0.0005"),
)
TOOL_PRICES = UnitPrices(currency=CURRENCY, tool_unit_price=Decimal("0.01"))
# 100 * 0.001 + 50 * 0.002 = 0.20; the tool bound is 3 * 0.01 = 0.03.
MODEL_BOUND = NormalizedUsage(input_tokens=100, output_tokens=50)
TOOL_BOUND = NormalizedUsage(tool_invocations=3)

COST_CONCURRENCY_TABLES = (
    "audit_append_receipts",
    "audit_chain_head",
    "audit_events",
    "audit_verifications",
    "cost_budget_reservations",
    "cost_budget_windows",
    "cost_budgets",
    "cost_pricing_rule_versions",
    "cost_usage_records",
)


class AdminSubject:
    """The verified platform subject; identity owns the real value."""

    def __init__(self, user_id: UUID, *, admin: bool = True) -> None:
        self.user_id = user_id
        self._admin = admin

    def has_platform_role(self, role: str) -> bool:
        return self._admin and role == "admin"


class OwnerSubject:
    """The verified workspace subject; the admission is injected."""

    def __init__(self, user_id: UUID) -> None:
        self.user_id = user_id


def ledger_actor() -> AuditActor:
    return AuditActor(actor_type="user", actor_id=str(WORKSPACE_OWNER_ID))


def publish_model_prices(session: Session, actor: AdminSubject) -> CostPricingRuleVersion:
    return costs.publish_pricing_version(
        session,
        actor=actor,
        subject_kind="model",
        subject_key=MODEL_SUBJECT,
        prices=MODEL_PRICES,
    )


def publish_tool_prices(session: Session, actor: AdminSubject) -> None:
    costs.publish_pricing_version(
        session, actor=actor, subject_kind="tool", subject_key=TOOL_SUBJECT, prices=TOOL_PRICES
    )


def create_limited_budget(
    session: Session,
    actor: OwnerSubject,
    *,
    limit_amount: Decimal,
    enforcement: str = "block",
    period: str = BUDGET_PERIOD_MONTHLY,
    workspace_id: UUID = WORKSPACE_ID,
) -> CostBudget:
    return costs.upsert_budget(
        session,
        actor=actor,
        workspace_id=workspace_id,
        currency=CURRENCY,
        limit_amount=limit_amount,
        unlimited=False,
        period=period,
        enforcement=enforcement,
        expected_version=None,
    )


def reserve_model(
    session: Session,
    operation_id: str,
    *,
    usage: NormalizedUsage = MODEL_BOUND,
    workspace_id: UUID | None = WORKSPACE_ID,
    run_ref: str | None = None,
) -> CostBudgetReservation:
    return costs.reserve_budget(
        session,
        actor=ledger_actor(),
        operation_id=operation_id,
        workspace_id=workspace_id,
        currency=CURRENCY,
        subject_kind="model",
        subject_key=MODEL_SUBJECT,
        usage_upper_bound=usage,
        run_ref=run_ref,
    )


def load_budget(session: Session, workspace_id: UUID = WORKSPACE_ID) -> CostBudget:
    budget = costs.read_budget(session, workspace_id=workspace_id, currency=CURRENCY)
    assert budget is not None
    return budget


def load_windows(session: Session, budget_id: UUID) -> dict[str, CostBudgetWindow]:
    rows = session.scalars(select(CostBudgetWindow).where(CostBudgetWindow.budget_id == budget_id))
    return {row.window_key: row for row in rows}


def pin_clock(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Pin the service clock so one window roll is observable in a single run."""

    class PinnedDatetime(datetime):  # type: ignore[type-arg]
        @classmethod
        def now(cls, tz=None):  # noqa: ANN201
            return moment.astimezone(tz) if tz is not None else moment

    monkeypatch.setattr(costs, "datetime", PinnedDatetime)


@pytest.fixture
def clean_cost_tables(database_engine):
    """Empty the cost and audit tables around a concurrency test."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE " + ", ".join(COST_CONCURRENCY_TABLES)))
    yield
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE " + ", ".join(COST_CONCURRENCY_TABLES)))


class TestPublishing:
    def test_versions_append_and_the_newest_prices_new_work(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        with database_sessions() as session, session.begin():
            first = publish_model_prices(session, admin)
            assert first.version == 1
            second = costs.publish_pricing_version(
                session,
                actor=admin,
                subject_kind="model",
                subject_key=MODEL_SUBJECT,
                prices=UnitPrices(
                    currency=CURRENCY,
                    input_price=Decimal("0.002"),
                    output_price=Decimal("0.004"),
                ),
            )
            assert second.version == 2
            versions, total = costs.list_pricing_versions(
                session,
                subject_kind="model",
                subject_key=MODEL_SUBJECT,
                page=PageParams(limit=10, offset=0),
            )
            assert total == 2
            assert [item.version for item in versions] == [2, 1]
            held = reserve_model(session, "op-price-pin")
            assert held.pricing_rule_version_id == second.id
            assert held.max_cost == quote_max_cost(
                prices=unit_prices_of_version(second),
                subject_kind="model",
                usage_upper_bound=MODEL_BOUND,
            )

    def test_a_reservation_keeps_its_pinned_prices_after_a_new_version(
        self, database_sessions
    ) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        with database_sessions() as session, session.begin():
            first = publish_model_prices(session, admin)
            held = reserve_model(session, "op-pinned")
            assert held.pricing_rule_version_id == first.id
        with database_sessions() as session, session.begin():
            costs.publish_pricing_version(
                session,
                actor=admin,
                subject_kind="model",
                subject_key=MODEL_SUBJECT,
                prices=UnitPrices(
                    currency=CURRENCY,
                    input_price=Decimal("0.01"),
                    output_price=Decimal("0.01"),
                ),
            )
        with database_sessions() as session, session.begin():
            record = costs.settle_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-pinned",
                actual_usage=NormalizedUsage(input_tokens=100, output_tokens=50),
            )
        assert record.actual_cost == Decimal("0.200000000000")

    def test_only_a_platform_admin_publishes_pricing(self, database_sessions) -> None:
        with database_sessions() as session, session.begin(), pytest.raises(InvalidArgumentError):
            costs.publish_pricing_version(
                session,
                actor=AdminSubject(PLATFORM_ADMIN_ID, admin=False),
                subject_kind="model",
                subject_key=MODEL_SUBJECT,
                prices=MODEL_PRICES,
            )

    def test_an_unknown_subject_kind_is_refused(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        with database_sessions() as session, session.begin(), pytest.raises(InvalidArgumentError):
            costs.publish_pricing_version(
                session,
                actor=admin,
                subject_kind="cluster",
                subject_key=MODEL_SUBJECT,
                prices=MODEL_PRICES,
            )


class TestBudgetStates:
    def test_no_budget_row_is_unconfigured_and_admitted(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            held = reserve_model(session, "op-unconfigured")
            assert held.budget_state == "unconfigured"
            assert held.budget_id is None
            assert held.window_key is None
            assert costs.read_budget(session, workspace_id=WORKSPACE_ID, currency=CURRENCY) is None
            record = costs.settle_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-unconfigured",
                actual_usage=MODEL_BOUND,
            )
        assert record.actual_cost == Decimal("0.200000000000")

    def test_an_unlimited_budget_admits_and_records_its_decision(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            budget = costs.upsert_budget(
                session,
                actor=owner,
                workspace_id=WORKSPACE_ID,
                currency=CURRENCY,
                limit_amount=None,
                unlimited=True,
                period=BUDGET_PERIOD_MONTHLY,
                enforcement="block",
                expected_version=None,
            )
            assert budget.unlimited is True
            assert budget.unlimited_decided_by_user_id == WORKSPACE_OWNER_ID
            assert budget.unlimited_decided_at is not None
            held = reserve_model(session, "op-unlimited")
            assert held.budget_state == "unlimited"
            assert held.budget_id == budget.id
            assert held.window_key == budget.current_window_key

    def test_a_limited_budget_admits_within_its_limit(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            held = reserve_model(session, "op-limited")
            assert held.budget_state == "limited"
            assert load_budget(session).window_reserved == Decimal("0.200000000000")

    def test_block_enforcement_refuses_a_projected_overrun(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("0.1"))
            with pytest.raises(costs.BudgetExceededError) as exceeded:
                reserve_model(session, "op-overrun")
            assert exceeded.value.code == "budget_exceeded"
            assert exceeded.value.status_code == 429
            assert load_budget(session).window_reserved == Decimal("0.000000000000")

    def test_a_held_reservation_blocks_the_next_overrun(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("0.3"))
            reserve_model(session, "op-first")
            with pytest.raises(costs.BudgetExceededError):
                reserve_model(session, "op-second")

    def test_warn_enforcement_admits_and_records_the_warning(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("0.1"), enforcement="warn")
            held = reserve_model(session, "op-warned")
            assert held.status == "reserved"
            warnings = session.scalars(
                select(AuditEvent).where(AuditEvent.action == "governance.cost_budget_warning")
            )
            assert len(list(warnings)) == 1

    def test_a_subject_without_pricing_cannot_reserve(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            with pytest.raises(InvalidArgumentError):
                costs.reserve_budget(
                    session,
                    actor=ledger_actor(),
                    operation_id="op-no-pricing",
                    workspace_id=WORKSPACE_ID,
                    currency=CURRENCY,
                    subject_kind="tool",
                    subject_key=TOOL_SUBJECT,
                    usage_upper_bound=TOOL_BOUND,
                )

    def test_a_tool_reservation_prices_per_invocation(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        with database_sessions() as session, session.begin():
            publish_tool_prices(session, admin)
            held = costs.reserve_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-tool",
                workspace_id=None,
                currency=CURRENCY,
                subject_kind="tool",
                subject_key=TOOL_SUBJECT,
                usage_upper_bound=TOOL_BOUND,
            )
            assert held.max_cost == Decimal("0.030000000000")


class TestReservationReplay:
    def test_the_same_operation_and_input_replays_one_reservation(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            first = reserve_model(session, "op-replay", run_ref="run-1")
            again = reserve_model(session, "op-replay", run_ref="run-1")
            assert again.id == first.id
            assert load_budget(session).window_reserved == Decimal("0.200000000000")
            rows = session.scalars(
                select(CostBudgetReservation).where(
                    CostBudgetReservation.operation_id == "op-replay"
                )
            )
            assert len(list(rows)) == 1

    def test_a_replayed_operation_with_another_input_conflicts(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            reserve_model(session, "op-replay")
            with pytest.raises(ConflictError):
                reserve_model(
                    session, "op-replay", usage=NormalizedUsage(input_tokens=200, output_tokens=50)
                )


class TestReleaseMatrix:
    def test_settlement_releases_the_unused_remainder(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            reserve_model(session, "op-settle")
            record = costs.settle_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-settle",
                actual_usage=NormalizedUsage(input_tokens=50, output_tokens=10),
            )
            budget = load_budget(session)
            assert record.actual_cost == Decimal("0.070000000000")
            assert record.overage_amount == Decimal("0.000000000000")
            assert budget.window_used == Decimal("0.070000000000")
            assert budget.window_reserved == Decimal("0.000000000000")

    def test_the_actual_cost_above_the_bound_is_recorded_in_full(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            reserve_model(session, "op-over")
            record = costs.settle_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-over",
                actual_usage=NormalizedUsage(input_tokens=500, output_tokens=100),
            )
            budget = load_budget(session)
            assert record.actual_cost == Decimal("0.700000000000")
            assert record.overage_amount == Decimal("0.500000000000")
            assert budget.window_used == Decimal("0.700000000000")

    def test_a_provably_unexecuted_operation_releases_entirely(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            reserve_model(session, "op-not-run")
            released = costs.release_budget(
                session, actor=ledger_actor(), operation_id="op-not-run", reason="never dispatched"
            )
            budget = load_budget(session)
            assert released.status == "released"
            assert released.released_reason == "never dispatched"
            assert budget.window_reserved == Decimal("0.000000000000")

    def test_a_settled_reservation_cannot_release(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            reserve_model(session, "op-settled")
            costs.settle_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-settled",
                actual_usage=MODEL_BOUND,
            )
            with pytest.raises(ConflictError):
                costs.release_budget(
                    session, actor=ledger_actor(), operation_id="op-settled", reason="too late"
                )

    def test_an_uncertain_reservation_does_not_release(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            reserve_model(session, "op-unknown")
            costs.mark_uncertain(
                session,
                actor=ledger_actor(),
                operation_id="op-unknown",
                reason="provider timeout",
            )
            with pytest.raises(ConflictError):
                costs.release_budget(
                    session,
                    actor=ledger_actor(),
                    operation_id="op-unknown",
                    reason="run was cancelled",
                )

    def test_a_released_reservation_cannot_settle(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            reserve_model(session, "op-gone")
            costs.release_budget(
                session, actor=ledger_actor(), operation_id="op-gone", reason="never dispatched"
            )
            with pytest.raises(ConflictError):
                costs.settle_budget(
                    session,
                    actor=ledger_actor(),
                    operation_id="op-gone",
                    actual_usage=MODEL_BOUND,
                )

    def test_a_replayed_settlement_returns_its_record_and_conflicts_on_change(
        self, database_sessions
    ) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            reserve_model(session, "op-replay-settle")
            first = costs.settle_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-replay-settle",
                actual_usage=MODEL_BOUND,
            )
            again = costs.settle_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-replay-settle",
                actual_usage=MODEL_BOUND,
            )
            assert again.id == first.id
            with pytest.raises(ConflictError):
                costs.settle_budget(
                    session,
                    actor=ledger_actor(),
                    operation_id="op-replay-settle",
                    actual_usage=NormalizedUsage(input_tokens=10, output_tokens=10),
                )

    def test_a_cancellation_confirmation_keeps_the_hold_until_reconciled(
        self, database_sessions
    ) -> None:
        """A stop signal proves execution stopped, never that no cost occurred."""
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            reserve_model(session, "op-cancelled")
            held = costs.mark_uncertain(
                session,
                actor=ledger_actor(),
                operation_id="op-cancelled",
                reason="run cancelled before completion",
                evidence={"signal": "cancel"},
            )
            budget = load_budget(session)
            assert held.status == "uncertain"
            assert held.evidence == {"signal": "cancel"}
            assert budget.window_reserved == Decimal("0.200000000000")
            settled = costs.reconcile_reservation(
                session,
                actor=ledger_actor(),
                operation_id="op-cancelled",
                outcome=RECONCILE_NOT_EXECUTED,
                actual_usage=None,
                evidence={"provider_receipt": "none"},
                expected_budget_revision=budget.revision,
            )
            assert settled.status == "released"
            assert load_budget(session).window_reserved == Decimal("0.000000000000")


class TestUncertainReconcile:
    def _hold_uncertain(self, session: Session) -> CostBudget:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        publish_model_prices(session, admin)
        budget = create_limited_budget(session, owner, limit_amount=Decimal("1"))
        reserve_model(session, "op-uncertain")
        costs.mark_uncertain(
            session,
            actor=ledger_actor(),
            operation_id="op-uncertain",
            reason="model call outcome unknown",
        )
        return budget

    def test_an_uncertain_reservation_settles_through_reconciliation_only(
        self, database_sessions
    ) -> None:
        with database_sessions() as session, session.begin():
            self._hold_uncertain(session)
            with pytest.raises(ConflictError):
                costs.settle_budget(
                    session,
                    actor=ledger_actor(),
                    operation_id="op-uncertain",
                    actual_usage=MODEL_BOUND,
                )
            with pytest.raises(InvalidArgumentError):
                costs.reconcile_reservation(
                    session,
                    actor=ledger_actor(),
                    operation_id="op-uncertain",
                    outcome=RECONCILE_SUCCEEDED,
                    actual_usage=None,
                    evidence={},
                    expected_budget_revision=1,
                )

    def test_a_held_reservation_does_not_reconcile(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            reserve_model(session, "op-held")
            with pytest.raises(ConflictError):
                costs.reconcile_reservation(
                    session,
                    actor=ledger_actor(),
                    operation_id="op-held",
                    outcome=RECONCILE_NOT_EXECUTED,
                    actual_usage=None,
                    evidence={},
                    expected_budget_revision=1,
                )

    def test_a_succeeded_reconciliation_settles_the_attested_usage(self, database_sessions) -> None:
        with database_sessions() as session, session.begin():
            budget = self._hold_uncertain(session)
            revision = budget.revision
        with database_sessions() as session, session.begin():
            settled = costs.reconcile_reservation(
                session,
                actor=ledger_actor(),
                operation_id="op-uncertain",
                outcome=RECONCILE_SUCCEEDED,
                actual_usage=NormalizedUsage(input_tokens=500, output_tokens=100),
                evidence={"provider_receipt": "rcpt-1"},
                expected_budget_revision=revision,
            )
            budget = load_budget(session)
            assert settled.status == "settled"
            assert settled.settled_amount == Decimal("0.700000000000")
            assert settled.overage_amount == Decimal("0.500000000000")
            assert budget.window_used == Decimal("0.700000000000")
            assert budget.window_reserved == Decimal("0.000000000000")
            records = session.scalars(
                select(CostUsageRecord).where(CostUsageRecord.operation_id == "op-uncertain")
            )
            assert len(list(records)) == 1

    def test_a_failed_reconciliation_stays_uncertain_with_new_evidence(
        self, database_sessions
    ) -> None:
        with database_sessions() as session, session.begin():
            budget = self._hold_uncertain(session)
            revision = budget.revision
        with database_sessions() as session, session.begin():
            still = costs.reconcile_reservation(
                session,
                actor=ledger_actor(),
                operation_id="op-uncertain",
                outcome=RECONCILE_FAILED,
                actual_usage=None,
                evidence={"attempt": 2},
                expected_budget_revision=revision,
            )
            assert still.status == "uncertain"
            assert still.evidence == {"attempt": 2}
            assert load_budget(session).window_reserved == Decimal("0.200000000000")

    def test_a_reconciliation_requires_the_seen_budget_revision(self, database_sessions) -> None:
        with database_sessions() as session, session.begin():
            budget = self._hold_uncertain(session)
            revision = budget.revision
        with database_sessions() as session, session.begin():
            owner = OwnerSubject(WORKSPACE_OWNER_ID)
            costs.upsert_budget(
                session,
                actor=owner,
                workspace_id=WORKSPACE_ID,
                currency=CURRENCY,
                limit_amount=Decimal("5"),
                unlimited=False,
                period=BUDGET_PERIOD_MONTHLY,
                enforcement="block",
                expected_version=revision,
            )
            with pytest.raises(ConflictError):
                costs.reconcile_reservation(
                    session,
                    actor=ledger_actor(),
                    operation_id="op-uncertain",
                    outcome=RECONCILE_NOT_EXECUTED,
                    actual_usage=None,
                    evidence={},
                    expected_budget_revision=revision,
                )

    def test_an_unconfigured_budget_carries_no_revision(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            reserve_model(session, "op-uncertain", workspace_id=None)
            costs.mark_uncertain(
                session,
                actor=ledger_actor(),
                operation_id="op-uncertain",
                reason="model call outcome unknown",
            )
            with pytest.raises(ConflictError):
                costs.reconcile_reservation(
                    session,
                    actor=ledger_actor(),
                    operation_id="op-uncertain",
                    outcome=RECONCILE_NOT_EXECUTED,
                    actual_usage=None,
                    evidence={},
                    expected_budget_revision=1,
                )

    def test_an_unknown_reconcile_outcome_is_refused(self, database_sessions) -> None:
        with database_sessions() as session, session.begin():
            budget = self._hold_uncertain(session)
            with pytest.raises(InvalidArgumentError):
                costs.reconcile_reservation(
                    session,
                    actor=ledger_actor(),
                    operation_id="op-uncertain",
                    outcome="maybe",
                    actual_usage=None,
                    evidence={},
                    expected_budget_revision=budget.revision,
                )


class TestWindowRoll:
    def test_a_reservation_settles_into_its_own_closed_window(
        self, database_sessions, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("10"))
        pin_clock(monkeypatch, datetime(2026, 1, 15, 12, tzinfo=UTC))
        with database_sessions() as session, session.begin():
            held = reserve_model(session, "op-january")
            assert held.window_key == "2026-01"
        pin_clock(monkeypatch, datetime(2026, 2, 20, 12, tzinfo=UTC))
        with database_sessions() as session, session.begin():
            reserve_model(session, "op-february")
            budget = load_budget(session)
            windows = load_windows(session, budget.id)
            assert budget.current_window_key == "2026-02"
            assert windows["2026-01"].reserved == Decimal("0.200000000000")
            assert budget.window_reserved == Decimal("0.200000000000")
        with database_sessions() as session, session.begin():
            record = costs.settle_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-january",
                actual_usage=NormalizedUsage(input_tokens=100, output_tokens=25),
            )
            budget = load_budget(session)
            windows = load_windows(session, budget.id)
            assert record.window_key == "2026-01"
            assert windows["2026-01"].used == Decimal("0.150000000000")
            assert windows["2026-01"].reserved == Decimal("0.000000000000")
            assert budget.window_used == Decimal("0.000000000000")
            assert budget.window_reserved == Decimal("0.200000000000")

    def test_a_none_period_keeps_no_window(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(
                session, owner, limit_amount=Decimal("10"), period=BUDGET_PERIOD_NONE
            )
            held = reserve_model(session, "op-no-window")
            assert held.window_key is None
            record = costs.settle_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-no-window",
                actual_usage=MODEL_BOUND,
            )
            budget = load_budget(session)
            assert record.window_key is None
            assert budget.window_used == Decimal("0.200000000000")
            assert budget.window_reserved == Decimal("0.000000000000")
            assert load_windows(session, budget.id) == {}


class TestBudgetRevision:
    def test_an_update_requires_the_seen_revision(self, database_sessions) -> None:
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            budget = create_limited_budget(session, owner, limit_amount=Decimal("1"))
            assert budget.revision == 1
            with pytest.raises(ConflictError):
                costs.upsert_budget(
                    session,
                    actor=owner,
                    workspace_id=WORKSPACE_ID,
                    currency=CURRENCY,
                    limit_amount=Decimal("2"),
                    unlimited=False,
                    period=BUDGET_PERIOD_MONTHLY,
                    enforcement="block",
                    expected_version=None,
                )
            with pytest.raises(ConflictError):
                costs.upsert_budget(
                    session,
                    actor=owner,
                    workspace_id=WORKSPACE_ID,
                    currency=CURRENCY,
                    limit_amount=Decimal("2"),
                    unlimited=False,
                    period=BUDGET_PERIOD_MONTHLY,
                    enforcement="block",
                    expected_version=7,
                )
            changed = costs.upsert_budget(
                session,
                actor=owner,
                workspace_id=WORKSPACE_ID,
                currency=CURRENCY,
                limit_amount=Decimal("2"),
                unlimited=False,
                period=BUDGET_PERIOD_MONTHLY,
                enforcement="warn",
                expected_version=1,
            )
            assert changed.revision == 2
            assert changed.enforcement == "warn"

    def test_a_new_budget_refuses_a_stated_revision(self, database_sessions) -> None:
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin(), pytest.raises(ConflictError):
            costs.upsert_budget(
                session,
                actor=owner,
                workspace_id=WORKSPACE_ID,
                currency=CURRENCY,
                limit_amount=Decimal("1"),
                unlimited=False,
                period=BUDGET_PERIOD_MONTHLY,
                enforcement="block",
                expected_version=3,
            )

    def test_lowering_the_limit_below_the_holdings_is_refused(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            create_limited_budget(session, owner, limit_amount=Decimal("1"))
            reserve_model(session, "op-held-amount")
            with pytest.raises(ConflictError):
                costs.upsert_budget(
                    session,
                    actor=owner,
                    workspace_id=WORKSPACE_ID,
                    currency=CURRENCY,
                    limit_amount=Decimal("0.1"),
                    unlimited=False,
                    period=BUDGET_PERIOD_MONTHLY,
                    enforcement="block",
                    expected_version=1,
                )

    def test_switching_the_period_closes_the_window(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            budget = create_limited_budget(session, owner, limit_amount=Decimal("10"))
            reserve_model(session, "op-before-switch")
            costs.settle_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-before-switch",
                actual_usage=MODEL_BOUND,
            )
            changed = costs.upsert_budget(
                session,
                actor=owner,
                workspace_id=WORKSPACE_ID,
                currency=CURRENCY,
                limit_amount=Decimal("10"),
                unlimited=False,
                period=BUDGET_PERIOD_NONE,
                enforcement="block",
                expected_version=budget.revision,
            )
            session.flush()
            windows = load_windows(session, changed.id)
            assert changed.current_window_key == ""
            assert len(windows) == 1
            closed = next(iter(windows.values()))
            assert closed.used == Decimal("0.200000000000")

    def test_an_unlimited_decision_is_recorded_and_revoked(self, database_sessions) -> None:
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            budget = costs.upsert_budget(
                session,
                actor=owner,
                workspace_id=WORKSPACE_ID,
                currency=CURRENCY,
                limit_amount=None,
                unlimited=True,
                period=BUDGET_PERIOD_MONTHLY,
                enforcement="block",
                expected_version=None,
            )
            assert budget.unlimited_decided_by_user_id == WORKSPACE_OWNER_ID
            changed = costs.upsert_budget(
                session,
                actor=owner,
                workspace_id=WORKSPACE_ID,
                currency=CURRENCY,
                limit_amount=Decimal("5"),
                unlimited=False,
                period=BUDGET_PERIOD_MONTHLY,
                enforcement="block",
                expected_version=budget.revision,
            )
            assert changed.unlimited is False
            assert changed.unlimited_decided_by_user_id is None
            assert changed.unlimited_decided_at is None


class TestLedgerComposition:
    def test_the_three_ledgers_commit_and_roll_back_together(self, database_sessions) -> None:
        admin = AdminSubject(PLATFORM_ADMIN_ID)
        owner = OwnerSubject(WORKSPACE_OWNER_ID)
        with database_sessions() as session, session.begin():
            publish_model_prices(session, admin)
            profile = users.create_user(
                session,
                email="compose@example.com",
                display_name="Composed",
                platform_roles=frozenset({"member"}),
                actor=AuditActor(actor_type="platform"),
            )
            workspace = Workspace(
                name="Composed",
                description="Three ledger composition",
                status="active",
                created_by_user_id=profile.id,
            )
            session.add(workspace)
            session.flush()
            ledger_user_id = profile.id
            workspace_id = workspace.id
            quotas.ensure_ledger(session, user_id=ledger_user_id, token_limit=1000)
            session.flush()
            quotas.set_limits(
                session,
                user_id=ledger_user_id,
                actor=ledger_actor(),
                token_limit=1000,
            )
            capacity.ensure_accounts(session, workspace_id=workspace_id, limits={})
            create_limited_budget(
                session, owner, limit_amount=Decimal("10"), workspace_id=workspace_id
            )
        with database_sessions() as session, session.begin():
            # The documented lock order: user quota, then space capacity, then
            # the budget; one caller transaction, one commit for all three.
            quotas.reserve_tokens(
                session,
                user_id=ledger_user_id,
                amount=500,
                idempotency_scope="execution.model_call",
                idempotency_key="tokens-0001",
            )
            capacity.reserve(
                session,
                workspace_id=workspace_id,
                kind=CAPACITY_CONCURRENCY,
                amount=1,
                idempotency_scope="execution.run",
                idempotency_key="capacity-0001",
            )
            reserve_model(
                session, "op-composed", workspace_id=workspace_id, run_ref="run-composed"
            )
        with database_sessions() as session:
            assert len(list(session.scalars(select(TokenReservation)))) == 1
            assert len(list(session.scalars(select(WorkspaceCapacityReservation)))) == 1
            assert len(list(session.scalars(select(CostBudgetReservation)))) == 1
            account = session.get(UserTokenAccount, ledger_user_id)
            assert account is not None and account.reserved_tokens == 500
            budget = load_budget(session, workspace_id)
            assert budget.window_reserved == Decimal("0.200000000000")
        with database_sessions() as session, pytest.raises(RuntimeError), session.begin():
            quotas.reserve_tokens(
                session,
                user_id=ledger_user_id,
                amount=500,
                idempotency_scope="execution.model_call",
                idempotency_key="tokens-0002",
            )
            capacity.reserve(
                session,
                workspace_id=workspace_id,
                kind=CAPACITY_CONCURRENCY,
                amount=1,
                idempotency_scope="execution.run",
                idempotency_key="capacity-0002",
            )
            reserve_model(
                session,
                "op-rolled-back",
                workspace_id=workspace_id,
                run_ref="run-rolled-back",
            )
            raise RuntimeError("the caller abandons the composed transaction")
        with database_sessions() as session:
            assert len(list(session.scalars(select(TokenReservation)))) == 1
            assert len(list(session.scalars(select(WorkspaceCapacityReservation)))) == 1
            assert len(list(session.scalars(select(CostBudgetReservation)))) == 1
            account = session.get(UserTokenAccount, ledger_user_id)
            assert account is not None and account.reserved_tokens == 500
            budget = load_budget(session, workspace_id)
            assert budget.window_reserved == Decimal("0.200000000000")

    def test_an_unknown_reservation_is_not_found(self, database_sessions) -> None:
        with database_sessions() as session, pytest.raises(NotFoundError):
            costs.settle_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-missing",
                actual_usage=MODEL_BOUND,
            )

    def test_an_unknown_reservation_status_filter_is_refused(self, database_sessions) -> None:
        with database_sessions() as session, pytest.raises(InvalidArgumentError):
            costs.list_reservations(
                session,
                workspace_id=WORKSPACE_ID,
                status="maybe",
                page=PageParams(limit=10, offset=0),
            )


def concurrent_sessions(engine) -> sessionmaker[Session]:
    """One sessionmaker per worker thread over the shared test engine."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        info={
            "dispatch_cipher": SecretCipher(
                key="JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=", key_id="test"
            ),
            "dispatch_trace_context": lambda: {},
        },
    )


def wait_for_lock_waits(engine, *, expected: int, timeout: float = 20.0) -> None:
    """Block until ``expected`` other backends wait for a lock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND pid <> pg_backend_pid() "
                    "AND datname = current_database()"
                )
            )
        if (waiting or 0) >= expected:
            return
        time.sleep(0.02)
    raise TimeoutError("the expected lock waits did not appear in time")


def seed_pricing_and_budget(engine, *, limit_amount: Decimal = Decimal("1")) -> None:
    sessions = concurrent_sessions(engine)
    admin = AdminSubject(PLATFORM_ADMIN_ID)
    owner = OwnerSubject(WORKSPACE_OWNER_ID)
    with sessions() as session, session.begin():
        publish_model_prices(session, admin)
        create_limited_budget(session, owner, limit_amount=limit_amount)


def test_a_forced_window_replays_one_reservation_under_the_unique_constraint(
    database_engine, clean_cost_tables
) -> None:
    """One operation id holds one row even when two producers race without a budget.

    With no budget row there is no budget lock to serialize on, so the
    interleaving is forced on the unique constraint instead: the holder keeps
    its insert in flight until the contender is observed waiting on it. The
    savepoint must roll back only the losing insert and replay the committed
    row instead of failing the contender's caller transaction.
    """
    sessions = concurrent_sessions(database_engine)
    seed_pricing_and_budget(database_engine)
    operation_id = f"op-race-{uuid4()}"
    holder = sessions()
    holder_transaction = holder.begin()
    contender_error: dict[str, str] = {}
    contender_id: dict[str, object] = {}
    try:
        held = reserve_model(holder, operation_id, workspace_id=None)
        contender = sessions()

        def contend() -> None:
            try:
                with contender, contender.begin():
                    replayed = reserve_model(contender, operation_id, workspace_id=None)
                contender_id["id"] = replayed.id
            except Exception as exc:  # noqa: BLE001 - the race must not surface one
                contender_error["name"] = type(exc).__name__

        thread = threading.Thread(target=contend)
        thread.start()
        # The contender is observed waiting on the holder's in-flight insert,
        # so it has committed to that insert; only now may the holder land.
        wait_for_lock_waits(database_engine, expected=1)
        holder_transaction.commit()
        thread.join(timeout=30)
    finally:
        holder.close()
    assert contender_error == {}
    assert contender_id["id"] == held.id
    with sessions() as session:
        rows = list(
            session.scalars(
                select(CostBudgetReservation).where(
                    CostBudgetReservation.operation_id == operation_id
                )
            )
        )
        events = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "governance.cost_budget_reserved"
                )
            )
        )
    assert len(rows) == 1
    assert len(events) == 1


def test_a_forced_window_serializes_admission_on_the_budget_row(
    database_engine, clean_cost_tables
) -> None:
    """A second reservation must see the first hold before it is admitted.

    The interleaving is forced, not raced: the holder commits its hold only
    after the contender is observed waiting on the budget row lock, so the
    contender cannot read a stale window total and oversubscribe the limit.
    """
    sessions = concurrent_sessions(database_engine)
    # One hold costs 0.20; the limit admits a single hold but not two, so the
    # contender's admission decision depends on seeing the holder's commit.
    seed_pricing_and_budget(database_engine, limit_amount=Decimal("0.3"))
    operation_id = f"op-window-{uuid4()}"
    holder = sessions()
    holder_transaction = holder.begin()
    contender_error: dict[str, str] = {}
    try:
        reserve_model(holder, operation_id)
        contender = sessions()

        def contend() -> None:
            try:
                with contender, contender.begin():
                    reserve_model(contender, f"op-contender-{uuid4()}")
            except Exception as exc:  # noqa: BLE001 - the refusal is the contract
                contender_error["name"] = type(exc).__name__

        thread = threading.Thread(target=contend)
        thread.start()
        # The contender is observed waiting on the budget row lock, so it has
        # read nothing yet; only now may the holder's hold become visible.
        wait_for_lock_waits(database_engine, expected=1)
        holder_transaction.commit()
        thread.join(timeout=30)
    finally:
        holder.close()
    assert contender_error == {"name": "BudgetExceededError"}
    with sessions() as session:
        budget = load_budget(session)
        assert budget.window_reserved == Decimal("0.200000000000")
        assert budget.window_used == Decimal("0.000000000000")


def test_a_stale_window_row_blocks_a_cross_window_settlement(database_sessions) -> None:
    """A reservation whose window never closed cannot settle into a missing row."""
    admin = AdminSubject(PLATFORM_ADMIN_ID)
    owner = OwnerSubject(WORKSPACE_OWNER_ID)
    with database_sessions() as session, session.begin():
        publish_model_prices(session, admin)
        budget = create_limited_budget(session, owner, limit_amount=Decimal("10"))
        held = reserve_model(session, "op-orphan")
        session.execute(
            update(CostBudget)
            .where(CostBudget.id == budget.id)
            .values(current_window_key="2000-02")
        )
        session.flush()
        assert held.window_key != "2000-02"
        with pytest.raises(NotFoundError):
            costs.settle_budget(
                session,
                actor=ledger_actor(),
                operation_id="op-orphan",
                actual_usage=MODEL_BOUND,
            )
