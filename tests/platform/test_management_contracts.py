"""Validation and evaluator boundaries do not require external services."""

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from opsmesh.platform.billing.contracts import ModelPricing
from opsmesh.platform.evaluations.contracts import DatasetCase, DatasetSpec, EvaluatorSpec
from opsmesh.platform.evaluations.evaluators import default_evaluators
from opsmesh.workflows.triggers.contracts import TriggerSpec


def test_prices_use_decimal_and_reject_nonfinite_values():
    price = ModelPricing(input_per_million="1.25", output_per_million="5")
    assert price.cost(1_000_000, 100_000) == Decimal("1.75000000")
    with pytest.raises(ValidationError):
        ModelPricing(input_per_million="NaN", output_per_million="1")


def test_dataset_identities_and_evaluator_revisions_are_explicit():
    case = DatasetCase(id="a", input="question", expected="answer")
    with pytest.raises(ValidationError):
        DatasetSpec(name="Repeated", cases=[case, case])
    registry = default_evaluators()
    assert registry.get(EvaluatorSpec(name="exact"))(case, "answer", {}).value == 1
    assert registry.get(EvaluatorSpec(name="exact"))(case, "wrong", {}).value == 0
    assert registry.get(EvaluatorSpec(name="contains"))(case, "the answer", {}).value == 1
    with pytest.raises(ValueError, match="revision"):
        registry.get(EvaluatorSpec(name="exact", revision="missing"))


def test_schedules_require_a_cron_and_known_timezone():
    payload = {"name": "Nightly", "target_kind": "agent", "target_id": uuid4()}
    with pytest.raises(ValidationError):
        TriggerSpec(kind="schedule", **payload)
    with pytest.raises(ValidationError):
        TriggerSpec(kind="webhook", cron="0 0 * * *", **payload)
    with pytest.raises(ValidationError):
        TriggerSpec(kind="schedule", cron="0 0 * * *", timezone="Missing/Timezone", **payload)
