"""S6.5 cost primitive tests: pricing computation and pure state rules.

These tests need no database: they cover the pure contracts the cost
ledger stores and derives — the price quote and its digest, the money
round-trips, the budget configuration states, the window key derivation
and the request models that refuse contradictory budgets.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from opsmesh.governance.costs.models import (
    BUDGET_ENFORCEMENTS,
    BUDGET_PERIODS,
    BUDGET_STATES,
    PRICING_SUBJECTS,
    RECONCILE_OUTCOMES,
    RESERVATION_STATUSES,
    CostBudget,
    CostPricingRuleVersion,
)
from opsmesh.governance.costs.pricing import (
    compute_cost,
    overage,
    prices_digest,
    quote_max_cost,
    unit_prices_of,
    unit_prices_of_version,
    unit_prices_payload,
    usage_of,
    usage_payload,
)
from opsmesh.governance.costs.schemas import (
    BudgetUpsertRequest,
    NormalizedUsage,
    UnitPrices,
)
from opsmesh.governance.costs.service import budget_state_of, window_key_for

MODEL_PRICES = UnitPrices(
    currency="USD",
    input_price=Decimal("0.0000025"),
    output_price=Decimal("0.00001"),
    cached_input_price=Decimal("0.00000125"),
)
TOOL_PRICES = UnitPrices(currency="USD", tool_unit_price=Decimal("0.05"))


class TestPricingComputation:
    def test_model_quote_counts_every_token_class(self) -> None:
        bound = NormalizedUsage(
            input_tokens=1_000_000, output_tokens=100_000, cached_input_tokens=500_000
        )
        assert quote_max_cost(
            prices=MODEL_PRICES, subject_kind="model", usage_upper_bound=bound
        ) == Decimal("0.0000025") * 1_000_000 + Decimal("0.00001") * 100_000 + Decimal(
            "0.00000125"
        ) * 500_000

    def test_tool_quote_counts_invocations(self) -> None:
        bound = NormalizedUsage(tool_invocations=3)
        assert quote_max_cost(
            prices=TOOL_PRICES, subject_kind="tool", usage_upper_bound=bound
        ) == Decimal("0.15")

    def test_tool_subject_without_a_unit_price_is_refused(self) -> None:
        with pytest.raises(ValueError):
            quote_max_cost(
                prices=MODEL_PRICES, subject_kind="tool", usage_upper_bound=NormalizedUsage()
            )

    def test_unknown_subject_kind_is_refused(self) -> None:
        with pytest.raises(ValueError):
            quote_max_cost(
                prices=MODEL_PRICES,
                subject_kind="embedding",
                usage_upper_bound=NormalizedUsage(),
            )

    def test_overage_is_the_excess_above_the_bound_and_never_negative(self) -> None:
        assert overage(max_cost=Decimal("1"), actual_cost=Decimal("1.5")) == Decimal("0.5")
        assert overage(max_cost=Decimal("1"), actual_cost=Decimal("0.5")) == Decimal("0")

    def test_digest_is_stable_and_price_sensitive(self) -> None:
        first = prices_digest(prices=MODEL_PRICES, subject_kind="model")
        again = prices_digest(prices=MODEL_PRICES, subject_kind="model")
        changed = prices_digest(
            prices=MODEL_PRICES.model_copy(
                update={"output_price": Decimal("0.0000101")}
            ),
            subject_kind="model",
        )
        assert first == again
        assert first != changed

    def test_price_and_usage_payloads_round_trip(self) -> None:
        prices = unit_prices_of(unit_prices_payload(MODEL_PRICES))
        usage = usage_of(usage_payload(NormalizedUsage(input_tokens=7, output_tokens=3)))
        assert prices == MODEL_PRICES
        assert usage == NormalizedUsage(input_tokens=7, output_tokens=3)

    def test_actual_cost_matches_the_quote_for_the_same_usage(self) -> None:
        usage = NormalizedUsage(input_tokens=10, output_tokens=4)
        quoted = quote_max_cost(
            prices=MODEL_PRICES, subject_kind="model", usage_upper_bound=usage
        )
        assert compute_cost(prices=MODEL_PRICES, subject_kind="model", usage=usage) == quoted

    def test_a_stored_version_row_converts_to_its_pinned_prices(self) -> None:
        version = CostPricingRuleVersion(
            subject_kind="model",
            subject_key="opsmesh:connection",
            version=1,
            currency="USD",
            input_price=Decimal("0.0000025"),
            output_price=Decimal("0.00001"),
            cached_input_price=Decimal("0.00000125"),
            digest=prices_digest(prices=MODEL_PRICES, subject_kind="model"),
        )
        assert unit_prices_of_version(version) == MODEL_PRICES


class TestBudgetStateRules:
    def test_no_row_is_unconfigured_and_never_unlimited(self) -> None:
        assert budget_state_of(None) == "unconfigured"

    def test_an_explicit_unlimited_row_is_unlimited(self) -> None:
        budget = CostBudget(workspace_id=None, currency="USD", unlimited=True)  # type: ignore[arg-type]
        assert budget_state_of(budget) == "unlimited"

    def test_a_limited_row_is_limited(self) -> None:
        budget = CostBudget(
            workspace_id=None, currency="USD", limit_amount=Decimal("10")  # type: ignore[arg-type]
        )
        assert budget_state_of(budget) == "limited"


class TestRequestContracts:
    def test_a_budget_declares_exactly_one_of_limit_or_unlimited(self) -> None:
        with pytest.raises(ValidationError):
            BudgetUpsertRequest.model_validate(
                {"currency": "USD", "limit_amount": 10, "unlimited": True}
            )
        with pytest.raises(ValidationError):
            BudgetUpsertRequest.model_validate({"currency": "USD"})

    def test_a_limited_budget_carries_a_positive_amount(self) -> None:
        with pytest.raises(ValidationError):
            BudgetUpsertRequest.model_validate({"currency": "USD", "limit_amount": 0})

    def test_the_period_and_enforcement_are_enumerated_values(self) -> None:
        assert BUDGET_PERIODS == ("monthly", "none")
        assert BUDGET_ENFORCEMENTS == ("warn", "block")
        assert BUDGET_STATES == ("unconfigured", "unlimited", "limited")
        assert PRICING_SUBJECTS == ("model", "tool")
        assert RESERVATION_STATUSES == ("reserved", "settled", "released", "uncertain")
        assert RECONCILE_OUTCOMES == ("not_executed", "succeeded", "failed")

    def test_usage_rejects_negative_counts(self) -> None:
        with pytest.raises(ValidationError):
            NormalizedUsage(input_tokens=-1)

    def test_unit_prices_reject_negative_prices(self) -> None:
        with pytest.raises(ValidationError):
            UnitPrices(currency="USD", input_price=Decimal("-1"))


class TestWindowKeyDerivation:
    def test_a_monthly_budget_keys_by_utc_month(self) -> None:
        assert window_key_for("monthly", datetime(2026, 10, 17, 23, 30, tzinfo=UTC)) == "2026-10"

    def test_a_none_period_has_no_window(self) -> None:
        assert window_key_for("none", datetime(2026, 10, 17, tzinfo=UTC)) is None
