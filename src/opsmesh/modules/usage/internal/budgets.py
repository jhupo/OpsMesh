"""Reservation math. Actual request costs are recorded independently, exactly once."""

from decimal import Decimal
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.usage.api import BudgetReservation, BudgetStatus, CostRecord


def budget_status(data: dict[str, Any], principal_id: str, currency: str) -> BudgetStatus:
    limit = Decimal(data["limits"][currency]) if currency in data["limits"] else None
    spent = Decimal(data["spent"].get(currency, "0"))
    reserved = sum(
        (
            Decimal(item["remaining"])
            for item in data["reservations"].values()
            if item["currency"] == currency and item["status"] != "settled"
        ),
        Decimal(0),
    )
    unresolved = sum(
        1
        for item in data["observations"].values()
        if item["cost"]["status"] in {"unpriced", "unavailable"}
    )
    return BudgetStatus(
        principal_id=principal_id,
        currency=currency,
        limit=limit,
        spent=spent,
        reserved=reserved,
        available=None if limit is None else limit - spent - reserved,
        unpriced_observations=unresolved,
    )


def reserve_budget(
    data: dict[str, Any], principal_id: str, reservation_id: str, amount: Decimal, currency: str
) -> BudgetReservation:
    if not reservation_id or not amount.is_finite() or amount < 0:
        raise OpsMeshError(
            "CONFIG_INVALID", "Reservation requires an id and a finite nonnegative amount"
        )
    prior = data["reservations"].get(reservation_id)
    if prior:
        reservation = BudgetReservation.model_validate(prior)
        if reservation.amount != amount or reservation.currency != currency:
            raise OpsMeshError("CONFLICT", "Reservation identity has different parameters")
        return reservation
    status = budget_status(data, principal_id, currency)
    if status.available is not None and status.available < amount:
        raise OpsMeshError("BUDGET_EXCEEDED", "Insufficient remaining budget")
    reservation = BudgetReservation(
        id=reservation_id,
        principal_id=principal_id,
        currency=currency,
        amount=amount,
        remaining=amount,
    )
    data["reservations"][reservation_id] = reservation.model_dump(mode="json")
    return reservation


def settle_budget(
    data: dict[str, Any], reservation_id: str, actual: Decimal | None
) -> BudgetReservation:
    prior = data["reservations"].get(reservation_id)
    if prior is None:
        raise OpsMeshError("NOT_FOUND", "Budget reservation not found")
    if actual is not None and (not actual.is_finite() or actual < 0):
        raise OpsMeshError("CONFIG_INVALID", "Settlement must be a finite nonnegative amount")
    reservation = BudgetReservation.model_validate(prior)
    if reservation.status == "settled":
        if reservation.actual != actual:
            raise OpsMeshError("CONFLICT", "Reservation was settled with another amount")
        return reservation
    result = reservation.model_copy(
        update={
            "status": "pending_pricing" if actual is None else "settled",
            "remaining": reservation.remaining if actual is None else Decimal(0),
            "actual": actual,
        }
    )
    data["reservations"][reservation_id] = result.model_dump(mode="json")
    return result


def apply_cost(data: dict[str, Any], cost: CostRecord, reservation_id: str | None) -> str | None:
    if cost.amount is None or cost.currency is None:
        return None
    currency = cost.currency
    data["spent"][currency] = str(Decimal(data["spent"].get(currency, "0")) + cost.amount)
    if reservation_id is None:
        return None
    prior = data["reservations"].get(reservation_id)
    if prior is None:
        return "UNKNOWN_RESERVATION"
    if prior["currency"] != currency:
        return "CURRENCY_MISMATCH"
    if prior["status"] != "settled":
        prior["remaining"] = str(max(Decimal(0), Decimal(prior["remaining"]) - cost.amount))
    return None
