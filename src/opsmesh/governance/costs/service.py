"""Governance cost service: pricing, budgets and the reservation ledger.

The reserve/settle/release contract is the whole point of this module:

- every charged operation reserves a pinned price sheet and a verifiable
  upper bound, whatever the budget configuration state is;
- settlement records the actual usage in full and releases the unused
  remainder; an explicitly unexecuted operation releases entirely;
- an unknown outcome is a persisted ``uncertain`` state with evidence —
  it keeps the reservation and grants no retry permission;
- reconciliation confirms an uncertain operation with its evidence and
  the budget revision the caller saw; it never re-executes anything.

Three budget configuration states are distinct facts: no row is
``unconfigured`` (admitted and recorded, never a silent unlimited),
``unlimited`` is an explicit recorded decision, and a limit carries its
period and enforcement. Lock order inside one caller transaction is the
budget row, then the reservation row; the caller composes the other
ledgers ahead of this one under the documented order.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.governance.costs.models import (
    BUDGET_ENFORCEMENT_BLOCK,
    BUDGET_ENFORCEMENTS,
    BUDGET_PERIOD_MONTHLY,
    BUDGET_PERIOD_NONE,
    BUDGET_PERIODS,
    BUDGET_STATE_LIMITED,
    BUDGET_STATE_UNCONFIGURED,
    BUDGET_STATE_UNLIMITED,
    MAX_CURRENCY_LENGTH,
    PRICING_STATUS_ACTIVE,
    PRICING_SUBJECTS,
    RECONCILE_FAILED,
    RECONCILE_NOT_EXECUTED,
    RECONCILE_OUTCOMES,
    RESERVATION_RELEASED,
    RESERVATION_RESERVED,
    RESERVATION_SETTLED,
    RESERVATION_STATUSES,
    RESERVATION_UNCERTAIN,
    CostBudget,
    CostBudgetReservation,
    CostBudgetWindow,
    CostPricingRuleVersion,
    CostUsageRecord,
)
from opsmesh.governance.costs.pricing import (
    compute_cost,
    overage,
    prices_digest,
    quote_max_cost,
    unit_prices_of,
    unit_prices_of_version,
    unit_prices_payload,
    usage_payload,
)
from opsmesh.governance.costs.schemas import NormalizedUsage, UnitPrices
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import (
    ConflictError,
    DomainError,
    InvalidArgumentError,
    NotFoundError,
)
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.serialization import payload_digest


class AdminSubject(Protocol):
    """The verified platform subject; identity owns the real value."""

    @property
    def user_id(self) -> UUID: ...

    def has_platform_role(self, role: str) -> bool: ...


class BudgetSubject(Protocol):
    """The verified workspace subject; the admission is injected."""

    @property
    def user_id(self) -> UUID: ...


class BudgetExceededError(DomainError):
    """The budget cannot admit this reservation's upper bound."""

    def __init__(
        self,
        message: str = "The cost budget cannot admit this reservation",
        *,
        code: str = "budget_exceeded",
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message, code=code, status_code=429, details=details or {})


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


def publish_pricing_version(
    session: Session,
    *,
    actor: AdminSubject,
    subject_kind: str,
    subject_key: str,
    prices: UnitPrices,
) -> CostPricingRuleVersion:
    """Publish one immutable price sheet; the newest version prices new work."""
    if not actor.has_platform_role("admin"):
        raise InvalidArgumentError("Only a platform administrator publishes pricing")
    _require_subject_kind(subject_kind)
    _require_currency(prices.currency)
    latest = session.scalar(
        select(CostPricingRuleVersion)
        .where(
            CostPricingRuleVersion.subject_kind == subject_kind,
            CostPricingRuleVersion.subject_key == subject_key,
        )
        .order_by(CostPricingRuleVersion.version.desc())
    )
    version = CostPricingRuleVersion(
        subject_kind=subject_kind,
        subject_key=subject_key,
        version=1 if latest is None else latest.version + 1,
        currency=prices.currency,
        input_price=prices.input_price,
        output_price=prices.output_price,
        cached_input_price=prices.cached_input_price,
        tool_unit_price=prices.tool_unit_price,
        digest=prices_digest(prices=prices, subject_kind=subject_kind),
        status=PRICING_STATUS_ACTIVE,
        published_by_user_id=actor.user_id,
    )
    session.add(version)
    session.flush()
    append_platform_event(
        session,
        actor=AuditActor(actor_type="platform", actor_id=str(actor.user_id)),
        action="governance.cost_pricing_published",
        target_type="cost_pricing_rule_version",
        target_id=str(version.id),
        summary="Cost pricing version published",
        details={
            "subject_kind": subject_kind,
            "subject_key": subject_key,
            "version": version.version,
            "digest": version.digest,
        },
    )
    return version


def list_pricing_versions(
    session: Session, *, subject_kind: str | None, subject_key: str | None, page: PageParams
) -> tuple[list[CostPricingRuleVersion], int]:
    """The published price sheets, newest version first per subject."""
    statement = select(CostPricingRuleVersion)
    if subject_kind is not None:
        statement = statement.where(CostPricingRuleVersion.subject_kind == subject_kind)
    if subject_key is not None:
        statement = statement.where(CostPricingRuleVersion.subject_key == subject_key)
    statement = statement.order_by(
        CostPricingRuleVersion.subject_kind,
        CostPricingRuleVersion.subject_key,
        CostPricingRuleVersion.version.desc(),
    )
    return page_scalars(session, statement, page)


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


def upsert_budget(
    session: Session,
    *,
    actor: BudgetSubject,
    workspace_id: UUID,
    currency: str,
    limit_amount: Decimal | None,
    unlimited: bool,
    period: str,
    enforcement: str,
    expected_version: int | None,
) -> CostBudget:
    """Create or change one workspace budget under its expected revision.

    An existing budget is never overwritten blindly: the caller states the
    revision it saw. Lowering a limit below the held window totals is
    refused, exactly like the capacity ledger, instead of silently
    over-committing the workspace.
    """
    _require_currency(currency)
    if period not in BUDGET_PERIODS:
        raise InvalidArgumentError(
            "The budget period is unknown",
            details={"period": period, "known_periods": list(BUDGET_PERIODS)},
        )
    if enforcement not in BUDGET_ENFORCEMENTS:
        raise InvalidArgumentError(
            "The budget enforcement is unknown",
            details={"enforcement": enforcement, "known_enforcements": list(BUDGET_ENFORCEMENTS)},
        )
    if unlimited and limit_amount is not None:
        raise InvalidArgumentError("An unlimited budget declares no limit amount")
    if not unlimited and limit_amount is None:
        raise InvalidArgumentError("A limited budget declares its limit amount")
    now = datetime.now(UTC)
    budget = session.scalar(
        select(CostBudget)
        .where(CostBudget.workspace_id == workspace_id, CostBudget.currency == currency)
        .with_for_update()
    )
    if budget is None:
        if expected_version is not None:
            raise ConflictError(
                "The budget does not exist at the stated revision",
                details={"expected_version": expected_version},
            )
        budget = CostBudget(
            workspace_id=workspace_id,
            currency=currency,
            limit_amount=limit_amount,
            unlimited=unlimited,
            period=period,
            enforcement=enforcement,
            revision=1,
            current_window_key=window_key_for(period, now) or "",
        )
        if unlimited:
            budget.unlimited_decided_by_user_id = actor.user_id
            budget.unlimited_decided_at = now
        session.add(budget)
        session.flush()
    else:
        if expected_version is None or expected_version != budget.revision:
            raise ConflictError(
                "The budget revision does not match the stated version",
                details={"revision": budget.revision, "expected_version": expected_version},
            )
        _reject_limit_below_holdings(budget, limit_amount=limit_amount)
        budget.limit_amount = limit_amount
        budget.unlimited = unlimited
        budget.enforcement = enforcement
        if unlimited:
            budget.unlimited_decided_by_user_id = actor.user_id
            budget.unlimited_decided_at = now
        else:
            budget.unlimited_decided_by_user_id = None
            budget.unlimited_decided_at = None
        if period != budget.period:
            _close_window(session, budget=budget, now=now)
            budget.period = period
            budget.current_window_key = window_key_for(period, now) or ""
        budget.revision += 1
    append_platform_event(
        session,
        actor=AuditActor(actor_type="user", actor_id=str(actor.user_id), workspace_id=workspace_id),
        action="governance.cost_budget_updated",
        target_type="cost_budget",
        target_id=str(budget.id),
        summary="Workspace cost budget updated",
        details={
            "currency": currency,
            "revision": budget.revision,
            "limit_amount": None if limit_amount is None else str(limit_amount),
            "unlimited": unlimited,
            "period": period,
            "enforcement": enforcement,
        },
    )
    return budget


def read_budget(session: Session, *, workspace_id: UUID, currency: str) -> CostBudget | None:
    """One workspace budget; no row is the unconfigured state, not unlimited."""
    return session.scalar(
        select(CostBudget).where(
            CostBudget.workspace_id == workspace_id, CostBudget.currency == currency
        )
    )


# ---------------------------------------------------------------------------
# The reservation ledger
# ---------------------------------------------------------------------------


def reserve_budget(
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
) -> CostBudgetReservation:
    """Hold one operation's verifiable cost upper bound.

    The pinned price sheet and the quoted maximum are stored whatever the
    budget state is. A limited budget with block enforcement refuses a
    projected overrun before any external call happens; warn enforcement
    admits and records the warning instead.
    """
    _require_subject_kind(subject_kind)
    _require_currency(currency)
    _require_operation_id(operation_id)
    version = _latest_pricing_version(session, subject_kind=subject_kind, subject_key=subject_key)
    if version is None:
        raise InvalidArgumentError(
            "No pricing rule is published for this subject",
            details={"subject_kind": subject_kind, "subject_key": subject_key},
        )
    prices = unit_prices_of_version(version)
    max_cost = quote_max_cost(
        prices=prices, subject_kind=subject_kind, usage_upper_bound=usage_upper_bound
    )
    declared = {
        "subject_kind": subject_kind,
        "subject_key": subject_key,
        "currency": currency,
        "max_cost": str(max_cost),
        "usage_upper_bound": usage_payload(usage_upper_bound),
        "run_ref": run_ref,
    }
    input_digest = payload_digest(declared)
    budget = _locked_budget(session, workspace_id=workspace_id, currency=currency)
    if budget is not None:
        _roll_window(session, budget=budget)
    existing = session.scalar(
        select(CostBudgetReservation).where(
            CostBudgetReservation.operation_id == operation_id
        )
    )
    if existing is not None:
        _require_same_input(existing.input_digest, declared)
        return existing
    budget_state = budget_state_of(budget)
    if budget is not None and budget_state == BUDGET_STATE_LIMITED:
        _admit_limited(
            session, actor=actor, budget=budget, max_cost=max_cost, operation_id=operation_id
        )
    window_key = None if budget is None else budget.current_window_key or None
    reservation = CostBudgetReservation(
        operation_id=operation_id,
        workspace_id=workspace_id,
        currency=currency,
        budget_id=None if budget is None else budget.id,
        budget_state=budget_state,
        window_key=window_key,
        subject_kind=subject_kind,
        subject_key=subject_key,
        pricing_rule_version_id=version.id,
        unit_prices=unit_prices_payload(prices),
        max_cost=max_cost,
        amount=max_cost,
        status=RESERVATION_RESERVED,
        run_ref=run_ref,
        input_digest=input_digest,
    )
    try:
        # The savepoint keeps the caller's transaction usable when a
        # concurrent producer of the same operation id lands first: the
        # unique constraint refuses the insert, the savepoint rolls back
        # only that insert, and the committed row is replayed.
        with session.begin_nested():
            session.add(reservation)
            session.flush()
    except IntegrityError as exc:
        landed = session.scalar(
            select(CostBudgetReservation).where(
                CostBudgetReservation.operation_id == operation_id
            )
        )
        if landed is None:
            raise ConflictError(
                "The reservation operation id already exists",
                details={"operation_id": operation_id},
            ) from exc
        _require_same_input(landed.input_digest, declared)
        return landed
    if budget is not None:
        budget.window_reserved += max_cost
    append_platform_event(
        session,
        actor=actor,
        action="governance.cost_budget_reserved",
        target_type="cost_budget_reservation",
        target_id=str(reservation.id),
        summary="Cost budget reserved",
        details={
            "operation_id": operation_id,
            "budget_state": budget_state,
            "max_cost": str(max_cost),
            "currency": currency,
        },
    )
    return reservation


def settle_budget(
    session: Session,
    *,
    actor: AuditActor,
    operation_id: str,
    actual_usage: NormalizedUsage,
) -> CostUsageRecord:
    """Settle one reservation with its actual usage and free the remainder.

    The actual cost is recorded in full even above the held upper bound;
    the excess is its own fact. A settled reservation replays its usage
    record for the same input and conflicts for a different one.
    """
    reservation = _locked_reservation(session, operation_id=operation_id)
    declared = {"actual_usage": usage_payload(actual_usage)}
    if reservation.status == RESERVATION_SETTLED:
        record = _usage_record(session, operation_id=operation_id)
        if record is None:  # pragma: no cover - settled always wrote its record
            raise ConflictError(
                "The settled reservation has no usage record",
                details={"operation_id": operation_id},
            )
        _require_same_input(
            payload_digest({"actual_usage": dict(record.usage)}), declared
        )
        return record
    if reservation.status == RESERVATION_RELEASED:
        raise ConflictError(
            "A released reservation cannot settle",
            details={"operation_id": operation_id},
        )
    if reservation.status == RESERVATION_UNCERTAIN:
        raise ConflictError(
            "An uncertain reservation settles through reconciliation",
            details={"operation_id": operation_id},
        )
    prices = unit_prices_of(reservation.unit_prices)
    actual_cost = compute_cost(
        prices=prices, subject_kind=reservation.subject_kind, usage=actual_usage
    )
    excess = overage(max_cost=reservation.max_cost, actual_cost=actual_cost)
    now = datetime.now(UTC)
    budget = _locked_budget_by_id(session, budget_id=reservation.budget_id)
    if budget is not None:
        _apply_window_delta(
            session,
            budget=budget,
            window_key=reservation.window_key,
            used_delta=actual_cost,
            reserved_delta=-reservation.amount,
        )
    reservation.status = RESERVATION_SETTLED
    reservation.settled_amount = actual_cost
    reservation.overage_amount = excess
    reservation.settled_at = now
    record = CostUsageRecord(
        operation_id=operation_id,
        reservation_id=reservation.id,
        workspace_id=reservation.workspace_id,
        window_key=reservation.window_key,
        subject_kind=reservation.subject_kind,
        subject_key=reservation.subject_key,
        pricing_rule_version_id=reservation.pricing_rule_version_id,
        usage=usage_payload(actual_usage),
        actual_cost=actual_cost,
        overage_amount=excess,
        recorded_at=now,
    )
    session.add(record)
    session.flush()
    append_platform_event(
        session,
        actor=actor,
        action="governance.cost_budget_settled",
        target_type="cost_budget_reservation",
        target_id=str(reservation.id),
        summary="Cost budget settled",
        details={
            "operation_id": operation_id,
            "actual_cost": str(actual_cost),
            "overage_amount": str(excess),
        },
    )
    return record


def release_budget(
    session: Session,
    *,
    actor: AuditActor,
    operation_id: str,
    reason: str,
) -> CostBudgetReservation:
    """Release one held reservation whose execution provably never ran.

    A settled reservation is final and an uncertain one reconciles; only a
    held reservation with evidence of non-execution releases here.
    """
    reservation = _locked_reservation(session, operation_id=operation_id)
    if reservation.status == RESERVATION_RELEASED:
        return reservation
    if reservation.status == RESERVATION_SETTLED:
        raise ConflictError(
            "A settled reservation cannot release",
            details={"operation_id": operation_id},
        )
    if reservation.status == RESERVATION_UNCERTAIN:
        raise ConflictError(
            "An uncertain reservation reconciles, it does not release",
            details={"operation_id": operation_id},
        )
    now = datetime.now(UTC)
    budget = _locked_budget_by_id(session, budget_id=reservation.budget_id)
    if budget is not None:
        _apply_window_delta(
            session,
            budget=budget,
            window_key=reservation.window_key,
            used_delta=Decimal(0),
            reserved_delta=-reservation.amount,
        )
    reservation.status = RESERVATION_RELEASED
    reservation.released_at = now
    reservation.released_reason = reason
    append_platform_event(
        session,
        actor=actor,
        action="governance.cost_budget_released",
        target_type="cost_budget_reservation",
        target_id=str(reservation.id),
        summary="Cost budget released",
        details={"operation_id": operation_id, "reason": reason},
    )
    return reservation


def mark_uncertain(
    session: Session,
    *,
    actor: AuditActor,
    operation_id: str,
    reason: str,
    evidence: dict[str, object] | None = None,
) -> CostBudgetReservation:
    """Persist one unknown outcome; the reservation is kept, not released.

    This state is a reconciliation fact with its evidence. It grants no
    retry permission: a retrying activity must read it and route the
    operation to reconciliation instead of calling the model again.
    """
    reservation = _locked_reservation(session, operation_id=operation_id)
    if reservation.status == RESERVATION_UNCERTAIN:
        if (reservation.uncertain_reason or "") != reason:
            raise ConflictError(
                "The uncertain reason changed",
                details={"operation_id": operation_id},
            )
        return reservation
    if reservation.status != RESERVATION_RESERVED:
        raise ConflictError(
            "Only a held reservation can become uncertain",
            details={"operation_id": operation_id, "status": reservation.status},
        )
    reservation.status = RESERVATION_UNCERTAIN
    reservation.uncertain_reason = reason
    reservation.evidence = dict(evidence or {})
    append_platform_event(
        session,
        actor=actor,
        action="governance.cost_budget_uncertain",
        target_type="cost_budget_reservation",
        target_id=str(reservation.id),
        summary="Cost budget reservation is uncertain",
        details={"operation_id": operation_id, "reason": reason},
    )
    return reservation


def reconcile_reservation(
    session: Session,
    *,
    actor: AuditActor,
    operation_id: str,
    outcome: str,
    actual_usage: NormalizedUsage | None,
    evidence: dict[str, object],
    expected_budget_revision: int | None,
) -> CostBudgetReservation:
    """Confirm one uncertain operation with its evidence and revision.

    ``not_executed`` releases the held amount, ``succeeded`` settles the
    attested actual usage — above the upper bound included — and
    ``failed`` keeps the reservation uncertain with the new evidence. A
    confirmed outcome is final; nothing here re-executes an operation.
    """
    if outcome not in RECONCILE_OUTCOMES:
        raise InvalidArgumentError(
            "The reconcile outcome is unknown",
            details={"outcome": outcome, "known_outcomes": list(RECONCILE_OUTCOMES)},
        )
    reservation = _locked_reservation(session, operation_id=operation_id)
    if reservation.status != RESERVATION_UNCERTAIN:
        raise ConflictError(
            "Only an uncertain reservation reconciles",
            details={"operation_id": operation_id, "status": reservation.status},
        )
    budget = _locked_budget_by_id(session, budget_id=reservation.budget_id)
    if budget is not None:
        if expected_budget_revision != budget.revision:
            raise ConflictError(
                "The budget revision does not match the stated version",
                details={"revision": budget.revision, "expected_version": expected_budget_revision},
            )
    elif expected_budget_revision is not None:
        raise ConflictError(
            "An unconfigured budget carries no revision",
            details={"expected_version": expected_budget_revision},
        )
    now = datetime.now(UTC)
    if outcome == RECONCILE_FAILED:
        reservation.evidence = dict(evidence)
        append_platform_event(
            session,
            actor=actor,
            action="governance.cost_budget_reconciled",
            target_type="cost_budget_reservation",
            target_id=str(reservation.id),
            summary="Uncertain cost reservation stays uncertain",
            details={"operation_id": operation_id, "outcome": outcome},
        )
        return reservation
    if outcome == RECONCILE_NOT_EXECUTED:
        if budget is not None:
            _apply_window_delta(
                session,
                budget=budget,
                window_key=reservation.window_key,
                used_delta=Decimal(0),
                reserved_delta=-reservation.amount,
            )
        reservation.status = RESERVATION_RELEASED
        reservation.released_at = now
        reservation.released_reason = "reconciled not_executed"
        reservation.evidence = dict(evidence)
    else:
        if actual_usage is None:
            raise InvalidArgumentError("A succeeded outcome attests its actual usage")
        prices = unit_prices_of(reservation.unit_prices)
        actual_cost = compute_cost(
            prices=prices, subject_kind=reservation.subject_kind, usage=actual_usage
        )
        excess = overage(max_cost=reservation.max_cost, actual_cost=actual_cost)
        if budget is not None:
            _apply_window_delta(
                session,
                budget=budget,
                window_key=reservation.window_key,
                used_delta=actual_cost,
                reserved_delta=-reservation.amount,
            )
        reservation.status = RESERVATION_SETTLED
        reservation.settled_amount = actual_cost
        reservation.overage_amount = excess
        reservation.settled_at = now
        reservation.evidence = dict(evidence)
        session.add(
            CostUsageRecord(
                operation_id=operation_id,
                reservation_id=reservation.id,
                workspace_id=reservation.workspace_id,
                window_key=reservation.window_key,
                subject_kind=reservation.subject_kind,
                subject_key=reservation.subject_key,
                pricing_rule_version_id=reservation.pricing_rule_version_id,
                usage=usage_payload(actual_usage),
                actual_cost=actual_cost,
                overage_amount=excess,
                recorded_at=now,
            )
        )
    session.flush()
    append_platform_event(
        session,
        actor=actor,
        action="governance.cost_budget_reconciled",
        target_type="cost_budget_reservation",
        target_id=str(reservation.id),
        summary="Uncertain cost reservation reconciled",
        details={"operation_id": operation_id, "outcome": outcome},
    )
    return reservation


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def list_reservations(
    session: Session,
    *,
    workspace_id: UUID,
    status: str | None,
    page: PageParams,
) -> tuple[list[CostBudgetReservation], int]:
    """One workspace's reservations; the uncertain set is the pending work."""
    if status is not None and status not in RESERVATION_STATUSES:
        raise InvalidArgumentError(
            "The reservation status is unknown",
            details={"status": status, "known_statuses": list(RESERVATION_STATUSES)},
        )
    statement = select(CostBudgetReservation).where(
        CostBudgetReservation.workspace_id == workspace_id
    )
    if status is not None:
        statement = statement.where(CostBudgetReservation.status == status)
    statement = statement.order_by(CostBudgetReservation.created_at.desc())
    return page_scalars(session, statement, page)


def list_usage(
    session: Session, *, workspace_id: UUID, page: PageParams
) -> tuple[list[CostUsageRecord], int]:
    """One workspace's settled usage, newest first."""
    statement = (
        select(CostUsageRecord)
        .where(CostUsageRecord.workspace_id == workspace_id)
        .order_by(CostUsageRecord.recorded_at.desc())
    )
    return page_scalars(session, statement, page)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _admit_limited(
    session: Session,
    *,
    actor: AuditActor,
    budget: CostBudget,
    max_cost: Decimal,
    operation_id: str,
) -> None:
    projected = budget.window_used + budget.window_reserved + max_cost
    limit_amount = budget.limit_amount
    if limit_amount is None:  # pragma: no cover - limited always carries a limit
        return
    if projected <= limit_amount:
        return
    if budget.enforcement == BUDGET_ENFORCEMENT_BLOCK:
        raise BudgetExceededError(
            details={
                "workspace_id": str(budget.workspace_id),
                "currency": budget.currency,
                "limit_amount": str(limit_amount),
                "window_used": str(budget.window_used),
                "window_reserved": str(budget.window_reserved),
                "requested_max_cost": str(max_cost),
            }
        )
    append_platform_event(
        session,
        actor=actor,
        action="governance.cost_budget_warning",
        target_type="cost_budget",
        target_id=str(budget.id),
        summary="Cost budget projection exceeds the limit under warn enforcement",
        details={
            "operation_id": operation_id,
            "limit_amount": str(limit_amount),
            "projected": str(projected),
        },
    )


def _apply_window_delta(
    session: Session,
    *,
    budget: CostBudget,
    window_key: str | None,
    used_delta: Decimal,
    reserved_delta: Decimal,
) -> None:
    """Move one reservation's amounts inside its own admission window."""
    if window_key is None or window_key == budget.current_window_key:
        budget.window_used += used_delta
        budget.window_reserved += reserved_delta
        return
    window = session.scalar(
        select(CostBudgetWindow).where(
            CostBudgetWindow.budget_id == budget.id,
            CostBudgetWindow.window_key == window_key,
        )
    )
    if window is None:
        raise NotFoundError(
            "The reservation's budget window is unknown",
            details={"window_key": window_key},
        )
    window.used += used_delta
    window.reserved += reserved_delta


def _roll_window(session: Session, *, budget: CostBudget) -> None:
    """Close the elapsed period window and open the current one."""
    now = datetime.now(UTC)
    key = window_key_for(budget.period, now)
    if (key or "") == budget.current_window_key:
        return
    _close_window(session, budget=budget, now=now)
    budget.current_window_key = key or ""


def _close_window(session: Session, *, budget: CostBudget, now: datetime) -> None:
    if budget.period != BUDGET_PERIOD_MONTHLY or not budget.current_window_key:
        budget.window_used = Decimal(0)
        budget.window_reserved = Decimal(0)
        return
    session.add(
        CostBudgetWindow(
            budget_id=budget.id,
            window_key=budget.current_window_key,
            used=budget.window_used,
            reserved=budget.window_reserved,
            closed_at=now,
        )
    )
    budget.window_used = Decimal(0)
    budget.window_reserved = Decimal(0)


def window_key_for(period: str, now: datetime) -> str | None:
    """The window key one period attributes to; a none period has no window."""
    if period == BUDGET_PERIOD_NONE:
        return None
    return now.astimezone(UTC).strftime("%Y-%m")


def _latest_pricing_version(
    session: Session, *, subject_kind: str, subject_key: str
) -> CostPricingRuleVersion | None:
    return session.scalar(
        select(CostPricingRuleVersion)
        .where(
            CostPricingRuleVersion.subject_kind == subject_kind,
            CostPricingRuleVersion.subject_key == subject_key,
            CostPricingRuleVersion.status == PRICING_STATUS_ACTIVE,
        )
        .order_by(CostPricingRuleVersion.version.desc())
    )


def budget_state_of(budget: CostBudget | None) -> str:
    """The recorded configuration state one budget row represents."""
    if budget is None:
        return BUDGET_STATE_UNCONFIGURED
    if budget.unlimited:
        return BUDGET_STATE_UNLIMITED
    return BUDGET_STATE_LIMITED


def _locked_budget(
    session: Session, *, workspace_id: UUID | None, currency: str
) -> CostBudget | None:
    if workspace_id is None:
        return None
    return session.scalar(
        select(CostBudget)
        .where(CostBudget.workspace_id == workspace_id, CostBudget.currency == currency)
        .with_for_update()
    )


def _locked_budget_by_id(session: Session, *, budget_id: UUID | None) -> CostBudget | None:
    if budget_id is None:
        return None
    return session.scalar(
        select(CostBudget).where(CostBudget.id == budget_id).with_for_update()
    )


def _locked_reservation(session: Session, *, operation_id: str) -> CostBudgetReservation:
    reservation = session.scalar(
        select(CostBudgetReservation)
        .where(CostBudgetReservation.operation_id == operation_id)
        .with_for_update()
    )
    if reservation is None:
        raise NotFoundError(
            "The cost reservation does not exist", details={"operation_id": operation_id}
        )
    return reservation


def _usage_record(session: Session, *, operation_id: str) -> CostUsageRecord | None:
    return session.scalar(
        select(CostUsageRecord).where(CostUsageRecord.operation_id == operation_id)
    )


def _reject_limit_below_holdings(
    budget: CostBudget, *, limit_amount: Decimal | None
) -> None:
    if limit_amount is None:
        return
    held = budget.window_used + budget.window_reserved
    if held > limit_amount:
        raise ConflictError(
            "The budget limit is below the outstanding window totals",
            details={
                "limit_amount": str(limit_amount),
                "window_used": str(budget.window_used),
                "window_reserved": str(budget.window_reserved),
            },
        )


def _require_same_input(
    stored: str,
    declared: Mapping[str, object],
    *,
    message: str = "The operation was replayed with a different input",
) -> None:
    if stored != payload_digest(dict(declared)):
        raise ConflictError(message)


def _require_subject_kind(subject_kind: str) -> None:
    if subject_kind not in PRICING_SUBJECTS:
        raise InvalidArgumentError(
            "The pricing subject kind is unknown",
            details={"subject_kind": subject_kind, "known_kinds": list(PRICING_SUBJECTS)},
        )


def _require_currency(currency: str) -> None:
    if not currency or len(currency) > MAX_CURRENCY_LENGTH:
        raise InvalidArgumentError(
            "The currency is unknown",
            details={"currency": currency, "max_length": MAX_CURRENCY_LENGTH},
        )


def _require_operation_id(operation_id: str) -> None:
    if not operation_id.strip():
        raise InvalidArgumentError("A reservation requires its operation id")


__all__ = [
    "AdminSubject",
    "BudgetExceededError",
    "BudgetSubject",
    "budget_state_of",
    "list_pricing_versions",
    "list_reservations",
    "list_usage",
    "mark_uncertain",
    "publish_pricing_version",
    "read_budget",
    "reconcile_reservation",
    "release_budget",
    "reserve_budget",
    "settle_budget",
    "upsert_budget",
    "window_key_for",
]
