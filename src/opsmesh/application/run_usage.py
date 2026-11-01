"""Per-run usage observation, budget admission and idempotent settlement."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from opsmesh.framework.agents import AgentRunCompleted
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.usage.api import ResourceUsageFact, UsageFact

if TYPE_CHECKING:
    from .ports import ApplicationServices


class RunUsageAccounting:
    def __init__(self, services: ApplicationServices) -> None:
        self.services = services

    async def reserve(self, run: AgentRunView) -> None:
        settings = self.services.settings
        status = await self.services.usage.budget(run.principal_id, settings.budget_currency)
        if status.limit is not None:
            await self.services.usage.reserve(
                run.principal_id,
                run.id,
                Decimal(settings.run_reservation_amount),
                settings.budget_currency,
            )

    async def observe(self, run: AgentRunView, data: dict[str, Any]) -> None:
        await self.services.usage.record(
            UsageFact(
                call_id=f"{run.id}:{data['call_id']}",
                principal_id=run.principal_id,
                run_id=run.id,
                model_profile=str(
                    data.get("model_profile_ref")
                    or run.agent.model_profile_ref
                    or data.get("model")
                    or "unknown"
                ),
                input_tokens=int(data.get("input_tokens", 0)),
                output_tokens=int(data.get("output_tokens", 0)),
                cached_input_tokens=int(data.get("cached_tokens", 0)),
                reasoning_tokens=int(data.get("reasoning_tokens", 0)),
                provider_usage=data.get("provider_usage", {}),
                usage_available=bool(data.get("usage_available", True)),
                reservation_id=run.id,
            )
        )

    async def finish(self, run: AgentRunView, outcome: AgentRunCompleted) -> None:
        services = self.services
        if not await services.usage.has_request_usage(run.principal_id, run.id):
            usage = outcome.usage
            await services.usage.record(
                UsageFact(
                    call_id=f"{run.id}:aggregate:{run.attempt}",
                    principal_id=run.principal_id,
                    run_id=run.id,
                    model_profile=run.agent.model_profile_ref or run.agent.model or "unknown",
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    kind="cumulative",
                    sequence=run.attempt,
                    reservation_id=run.id,
                    usage_available=usage.usage_available and usage.requests > 0,
                )
            )
        await self.settle(run)

    async def observe_resource(self, run: AgentRunView, data: dict[str, Any]) -> None:
        await self.services.usage.record_resource(
            ResourceUsageFact(
                call_id=f"{run.id}:{data['call_id']}",
                principal_id=run.principal_id,
                run_id=run.id,
                resource_key=data["resource_key"],
                unit=data["unit"],
                quantity=Decimal(str(data["quantity"])),
                usage_available=bool(data.get("usage_available", True)),
                reservation_id=run.id,
            )
        )

    async def settle(self, run: AgentRunView) -> None:
        services = self.services
        status = await services.usage.budget(run.principal_id, services.settings.budget_currency)
        if status.limit is None:
            return
        costs = await services.usage.costs(run.principal_id, run.id)
        billable = [cost for cost in costs if cost.status != "ignored"]
        actual = (
            None
            if any(
                cost.amount is None or cost.currency != services.settings.budget_currency
                for cost in billable
            )
            else sum((cost.amount or Decimal(0) for cost in billable), Decimal(0))
        )
        try:
            await services.usage.settle(run.principal_id, run.id, actual)
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
