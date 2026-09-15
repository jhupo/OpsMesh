from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.utils import ensure_aware_utc
from backend.app.domains.agents.providers.policy import canonical_model_provider
from backend.app.domains.agents.runtime.contracts import AgentRunRequest, AgentRunResult
from backend.app.domains.orchestration.runs.models import AgentRun
from backend.app.observability.audit.service import AuditService
from backend.app.observability.costs.models import (
    ModelUsageRecord,
    WorkspaceCostBudget,
)
from backend.app.observability.costs.pricing import (
    CostPricingService,
    calculate_costs,
    normalize_currency,
)
from backend.app.observability.costs.queries import CostQueryService
from backend.app.observability.costs.usage import normalize_model_usage
from backend.app.observability.telemetry.trace_context import current_trace_context


class CostBudgetExceededError(RuntimeError):
    pass


class CostAccountingService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_usage(
        self,
        *,
        run: AgentRun,
        request: AgentRunRequest,
        result: AgentRunResult,
        job_attempt: int,
        occurred_at: datetime | None = None,
    ) -> ModelUsageRecord:
        existing = self._session.scalar(
            select(ModelUsageRecord).where(
                ModelUsageRecord.workspace_id == run.workspace_id,
                ModelUsageRecord.agent_run_id == run.id,
                ModelUsageRecord.job_attempt == job_attempt,
            )
        )
        if existing is not None:
            return existing

        occurred_at = ensure_aware_utc(occurred_at or datetime.now(UTC))
        provider = canonical_model_provider(request.provider or "openai")
        model = request.model or request.agent_profile.model
        usage = normalize_model_usage(result)
        pricing = CostPricingService(self._session).resolve_rule(
            workspace_id=run.workspace_id,
            provider=provider,
            model=model,
            occurred_at=occurred_at,
        )
        costs = (
            calculate_costs(usage, pricing) if pricing is not None and usage.available else None
        )
        trace = current_trace_context()
        record = ModelUsageRecord(
            id=uuid4(),
            workspace_id=run.workspace_id,
            agent_run_id=run.id,
            task_id=run.task_id,
            agent_profile_id=run.agent_profile_id,
            pricing_rule_id=pricing.id if pricing is not None else None,
            provider=provider,
            model=model,
            model_api=request.model_api,
            pricing_version=pricing.version if pricing is not None else None,
            currency=pricing.currency if pricing is not None else None,
            metering_status=(
                "missing_usage"
                if not usage.available
                else "priced"
                if pricing is not None
                else "unpriced"
            ),
            job_attempt=job_attempt,
            request_count=usage.request_count,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            total_tokens=usage.total_tokens,
            input_cost=costs[0] if costs is not None else None,
            output_cost=costs[1] if costs is not None else None,
            cached_input_cost=costs[2] if costs is not None else None,
            request_cost=costs[3] if costs is not None else None,
            total_cost=costs[4] if costs is not None else None,
            raw_usage=usage.raw_usage,
            trace_id=trace.trace_id if trace is not None else None,
            occurred_at=occurred_at,
        )
        self._session.add(record)
        self._session.flush([record])
        return record

    def assert_budget_available(
        self,
        workspace_id: UUID,
        *,
        provider: str,
        model: str,
        now: datetime | None = None,
    ) -> None:
        now = ensure_aware_utc(now or datetime.now(UTC))
        pricing = CostPricingService(self._session).resolve_rule(
            workspace_id=workspace_id,
            provider=canonical_model_provider(provider),
            model=model,
            occurred_at=now,
        )
        if pricing is None:
            blocking_budget_exists = self._session.scalar(
                select(func.count())
                .select_from(WorkspaceCostBudget)
                .where(
                    WorkspaceCostBudget.workspace_id == workspace_id,
                    WorkspaceCostBudget.enabled.is_(True),
                    WorkspaceCostBudget.enforcement == "block",
                )
            )
            if blocking_budget_exists:
                raise CostBudgetExceededError(
                    "workspace blocking budget requires an active model pricing rule"
                )
            return
        budget = self._session.scalar(
            select(WorkspaceCostBudget).where(
                WorkspaceCostBudget.workspace_id == workspace_id,
                WorkspaceCostBudget.currency == pricing.currency,
                WorkspaceCostBudget.enabled.is_(True),
                WorkspaceCostBudget.enforcement == "block",
            )
        )
        if budget is None:
            return
        status = CostQueryService(self._session).budget_status(
            workspace_id,
            currency=budget.currency,
            now=now,
        )
        if status.state == "exhausted":
            raise CostBudgetExceededError(
                f"workspace model cost budget exhausted for {budget.currency}"
            )

    def assert_projected_budget_available(
        self,
        workspace_id: UUID,
        *,
        estimated_cost: Decimal,
        currency: str = "USD",
        now: datetime | None = None,
    ) -> None:
        """Reject a project whose admitted model estimate would exceed a blocking budget."""
        if estimated_cost < 0:
            raise ValueError("estimated_cost must not be negative")
        normalized_currency = normalize_currency(currency)
        budget = self._session.scalar(
            select(WorkspaceCostBudget).where(
                WorkspaceCostBudget.workspace_id == workspace_id,
                WorkspaceCostBudget.currency == normalized_currency,
                WorkspaceCostBudget.enabled.is_(True),
                WorkspaceCostBudget.enforcement == "block",
            )
        )
        if budget is None:
            return
        status = CostQueryService(self._session).budget_status(
            workspace_id,
            currency=normalized_currency,
            now=now,
        )
        if (
            status.monthly_limit is not None
            and status.spent + estimated_cost > status.monthly_limit
        ):
            raise CostBudgetExceededError(
                f"workspace projected model cost exceeds {normalized_currency} budget"
            )

    def upsert_budget(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        currency: str,
        monthly_limit: Decimal,
        warning_ratio: Decimal,
        enforcement: str,
        enabled: bool,
    ) -> WorkspaceCostBudget:
        normalized_currency = normalize_currency(currency)
        if monthly_limit <= 0:
            raise ValueError("monthly_limit must be greater than zero")
        if warning_ratio <= 0 or warning_ratio > 1:
            raise ValueError("warning_ratio must be greater than zero and at most one")
        if enforcement not in {"warn", "block"}:
            raise ValueError("enforcement must be warn or block")
        budget = self._session.scalar(
            select(WorkspaceCostBudget).where(
                WorkspaceCostBudget.workspace_id == workspace_id,
                WorkspaceCostBudget.currency == normalized_currency,
            )
        )
        if budget is None:
            budget = WorkspaceCostBudget(
                workspace_id=workspace_id,
                currency=normalized_currency,
            )
            self._session.add(budget)
        budget.updated_by_user_id = actor_user_id
        budget.monthly_limit = monthly_limit
        budget.warning_ratio = warning_ratio
        budget.enforcement = enforcement
        budget.enabled = enabled
        self._session.flush([budget])
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="cost.budget_updated",
            target_type="workspace_cost_budget",
            target_id=budget.id,
            metadata={
                "currency": normalized_currency,
                "monthly_limit": str(monthly_limit),
                "warning_ratio": str(warning_ratio),
                "enforcement": enforcement,
                "enabled": enabled,
            },
        )
        return budget
