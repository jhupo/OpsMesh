"""Idempotency and cumulative-counter accounting, separate from price lookup."""

from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.usage.api import CostRecord, UsageFact

COUNTERS = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")


def prior_observation(data: dict[str, Any], fact: UsageFact) -> CostRecord | None:
    prior = data["observations"].get(fact.call_id)
    if prior is None:
        return None
    if prior.get("type") == "resource":
        raise OpsMeshError("CONFLICT", "Usage identity already belongs to a resource measurement")
    # A replay may be reconstructed later; wall-clock reconstruction is not a new measurement.
    incoming = fact.model_dump(mode="json", exclude={"occurred_at"})
    existing = UsageFact.model_validate(prior["fact"]).model_dump(
        mode="json", exclude={"occurred_at"}
    )
    if incoming != existing:
        raise OpsMeshError("CONFLICT", "Usage call identity has different counters or ownership")
    return CostRecord.model_validate(prior["cost"])


def usage_delta(data: dict[str, Any], fact: UsageFact) -> UsageFact | None:
    runs = data["runs"]
    prior = runs.get(fact.run_id)
    if prior and prior["kind"] != fact.kind:
        raise OpsMeshError(
            "USAGE_GRANULARITY_CONFLICT",
            "Request facts and cumulative totals cannot be billed together",
        )
    if fact.parent_run_id and runs.get(fact.parent_run_id, {}).get("kind") == "cumulative":
        raise OpsMeshError(
            "USAGE_GRANULARITY_CONFLICT", "A parent aggregate already covers this child run"
        )
    if fact.kind == "cumulative" and any(
        item.get("parent_run_id") == fact.run_id for item in runs.values()
    ):
        raise OpsMeshError("USAGE_GRANULARITY_CONFLICT", "Child runs have already been metered")
    if fact.kind == "request":
        runs[fact.run_id] = {"kind": "request", "parent_run_id": fact.parent_run_id}
        return fact
    if prior and prior.get("model_profile") != fact.model_profile:
        raise OpsMeshError(
            "USAGE_MODEL_MISMATCH", "Cumulative snapshots require a stable model profile"
        )
    if prior and fact.sequence <= prior["sequence"]:
        if fact.sequence == prior["sequence"] and any(
            getattr(fact, counter) != prior.get(counter, 0) for counter in COUNTERS
        ):
            raise OpsMeshError("CONFLICT", "Cumulative sequence has different counters")
        return None
    if not fact.usage_available:
        return fact
    delta = {
        counter: getattr(fact, counter) - (prior.get(counter, 0) if prior else 0)
        for counter in COUNTERS
    }
    if (
        any(value < 0 for value in delta.values())
        or delta["cached_input_tokens"] > delta["input_tokens"]
        or delta["reasoning_tokens"] > delta["output_tokens"]
    ):
        raise OpsMeshError("USAGE_COUNTER_REGRESSION", "Cumulative usage counters are inconsistent")
    runs[fact.run_id] = {
        "kind": "cumulative",
        "sequence": fact.sequence,
        "model_profile": fact.model_profile,
        "parent_run_id": fact.parent_run_id,
        **{counter: getattr(fact, counter) for counter in COUNTERS},
    }
    return fact.model_copy(update=delta)
