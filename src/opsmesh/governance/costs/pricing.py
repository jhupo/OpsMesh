"""Pure pricing computation for models and tools.

No database, no I/O, no configuration reads: the pinned prices and the
usage enter as values and the money leaves as Decimal. Every charged
operation quotes its verifiable upper bound here before any ledger
touches a row, so the bound is reproducible from the stored snapshot.
"""

from __future__ import annotations

from decimal import Decimal

from opsmesh.governance.costs.models import (
    PRICING_SUBJECT_MODEL,
    PRICING_SUBJECT_TOOL,
    PRICING_SUBJECTS,
    CostPricingRuleVersion,
)
from opsmesh.governance.costs.schemas import NormalizedUsage, UnitPrices
from opsmesh.shared.serialization import payload_digest

# Money is quantized to twelve decimal places; prices keep eighteen.
AMOUNT_QUANTUM = Decimal("0.000000000001")
ZERO = Decimal(0)


def quote_max_cost(
    *, prices: UnitPrices, subject_kind: str, usage_upper_bound: NormalizedUsage
) -> Decimal:
    """The verifiable upper bound of one operation, before it runs."""
    _require_subject_kind(subject_kind)
    if subject_kind == PRICING_SUBJECT_TOOL:
        if prices.tool_unit_price is None:
            raise ValueError("A tool subject requires its unit price")
        return _money(prices.tool_unit_price * usage_upper_bound.tool_invocations)
    cached = prices.cached_input_price if prices.cached_input_price is not None else ZERO
    return _money(
        prices.input_price * usage_upper_bound.input_tokens
        + prices.output_price * usage_upper_bound.output_tokens
        + cached * usage_upper_bound.cached_input_tokens
    )


def compute_cost(*, prices: UnitPrices, subject_kind: str, usage: NormalizedUsage) -> Decimal:
    """The actual cost of one operation from its reported usage."""
    return quote_max_cost(prices=prices, subject_kind=subject_kind, usage_upper_bound=usage)


def overage(*, max_cost: Decimal, actual_cost: Decimal) -> Decimal:
    """The part of an actual cost above the held upper bound, never negative."""
    return _money(max(ZERO, actual_cost - max_cost))


def prices_digest(*, prices: UnitPrices, subject_kind: str) -> str:
    """The stable digest of a pinned price sheet, stored with the reservation."""
    _require_subject_kind(subject_kind)
    return payload_digest(unit_prices_payload(prices) | {"subject_kind": subject_kind})


def unit_prices_payload(prices: UnitPrices) -> dict[str, object]:
    """The JSONB snapshot stored on a reservation; strings keep full precision."""
    return {
        "currency": prices.currency,
        "input_price": str(prices.input_price),
        "output_price": str(prices.output_price),
        "cached_input_price": (
            None if prices.cached_input_price is None else str(prices.cached_input_price)
        ),
        "tool_unit_price": (
            None if prices.tool_unit_price is None else str(prices.tool_unit_price)
        ),
    }


def unit_prices_of(payload: dict[str, object]) -> UnitPrices:
    """Read a stored snapshot back; an unknown shape is a hard failure."""
    return UnitPrices(
        currency=str(payload["currency"]),
        input_price=Decimal(str(payload["input_price"])),
        output_price=Decimal(str(payload["output_price"])),
        cached_input_price=(
            None
            if payload["cached_input_price"] is None
            else Decimal(str(payload["cached_input_price"]))
        ),
        tool_unit_price=(
            None
            if payload["tool_unit_price"] is None
            else Decimal(str(payload["tool_unit_price"]))
        ),
    )


def unit_prices_of_version(version: CostPricingRuleVersion) -> UnitPrices:
    """The pinned price sheet of one stored pricing version row."""
    return UnitPrices(
        currency=version.currency,
        input_price=version.input_price,
        output_price=version.output_price,
        cached_input_price=version.cached_input_price,
        tool_unit_price=version.tool_unit_price,
    )


def usage_payload(usage: NormalizedUsage) -> dict[str, object]:
    """The JSONB snapshot stored on a usage record."""
    return usage.model_dump()


def usage_of(payload: dict[str, object]) -> NormalizedUsage:
    """Read a stored usage snapshot back."""
    return NormalizedUsage.model_validate(payload)


def _money(value: Decimal) -> Decimal:
    return value.quantize(AMOUNT_QUANTUM)


def _require_subject_kind(subject_kind: str) -> None:
    if subject_kind not in PRICING_SUBJECTS:
        raise ValueError(f"Unknown pricing subject kind: {subject_kind}")


__all__ = [
    "PRICING_SUBJECT_MODEL",
    "PRICING_SUBJECT_TOOL",
    "compute_cost",
    "overage",
    "prices_digest",
    "quote_max_cost",
    "unit_prices_of",
    "unit_prices_of_version",
    "unit_prices_payload",
    "usage_of",
    "usage_payload",
]
