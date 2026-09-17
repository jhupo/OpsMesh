from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
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
from backend.app.observability.costs.usage import NormalizedModelUsage, normalize_model_usage
from backend.app.observability.notifications.service import GovernanceNotificationService
from backend.app.observability.telemetry.request_context import current_evidence_context


@dataclass(frozen=True)
class CostBudgetDecision:
    allowed: bool
    reason: str
    evaluated_at: datetime
    provider: str
    model: str
    pricing_rule_id: UUID | None
    pricing_version: str | None
    currency: str | None
    budget_id: UUID | None
    budget_state: str
    enforcement: str | None
    spent: Decimal | None
    monthly_limit: Decimal | None
    warning_ratio: Decimal | None
    utilization_ratio: Decimal | None

    def snapshot(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "evaluated_at": self.evaluated_at.isoformat(),
            "provider": self.provider,
            "model": self.model,
            "pricing_rule_id": str(self.pricing_rule_id)
            if self.pricing_rule_id is not None
            else None,
            "pricing_version": self.pricing_version,
            "currency": self.currency,
            "budget_id": str(self.budget_id) if self.budget_id is not None else None,
            "budget_state": self.budget_state,
            "enforcement": self.enforcement,
            "spent": _decimal_string(self.spent),
            "monthly_limit": _decimal_string(self.monthly_limit),
            "warning_ratio": _decimal_string(self.warning_ratio),
            "utilization_ratio": _decimal_string(self.utilization_ratio),
        }


class CostBudgetExceededError(RuntimeError):
    def __init__(
        self,
        message: str,
        decision: CostBudgetDecision | None = None,
    ) -> None:
        super().__init__(message)
        self.decision = decision


class CostAccountingService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_attempt(
        self,
        *,
        run: AgentRun,
        request: AgentRunRequest,
        result: AgentRunResult | None,
        job_attempt: int,
        request_sequence: int,
        attempt_outcome: str,
        budget_decision: CostBudgetDecision,
        error: Exception | None = None,
        occurred_at: datetime | None = None,
    ) -> ModelUsageRecord:
        if attempt_outcome not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("attempt_outcome must be succeeded, failed, or cancelled")
        existing = self._session.scalar(
            select(ModelUsageRecord).where(
                ModelUsageRecord.workspace_id == run.workspace_id,
                ModelUsageRecord.agent_run_id == run.id,
                ModelUsageRecord.job_attempt == job_attempt,
                ModelUsageRecord.request_sequence == request_sequence,
            )
        )
        if existing is not None:
            return existing

        occurred_at = ensure_aware_utc(occurred_at or datetime.now(UTC))
        provider = canonical_model_provider(request.provider or "openai")
        model = request.model or request.agent_profile.model
        usage = normalize_model_usage(result) if result is not None else _missing_usage()
        pricing = CostPricingService(self._session).resolve_rule(
            workspace_id=run.workspace_id,
            provider=provider,
            model=model,
            occurred_at=occurred_at,
        )
        costs = (
            calculate_costs(usage, pricing) if pricing is not None and usage.available else None
        )
        evidence = current_evidence_context()
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
            request_sequence=request_sequence,
            attempt_outcome=attempt_outcome,
            error_code=type(error).__name__ if error is not None else None,
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
            budget_decision=budget_decision.snapshot(),
            request_id=evidence.get("request_id"),
            trace_id=evidence.get("trace_id"),
            span_id=evidence.get("span_id"),
            worker_id=evidence.get("worker_id"),
            runtime_id=evidence.get("runtime_id"),
            occurred_at=occurred_at,
        )
        self._session.add(record)
        self._session.flush([record])
        GovernanceNotificationService(self._session).record_model_attempt(record)
        return record

    def assert_budget_available(
        self,
        workspace_id: UUID,
        *,
        provider: str,
        model: str,
        now: datetime | None = None,
    ) -> CostBudgetDecision:
        decision = self.evaluate_budget(
            workspace_id,
            provider=provider,
            model=model,
            now=now,
        )
        if not decision.allowed:
            message = (
                "workspace blocking budget requires an active model pricing rule"
                if decision.reason == "pricing_rule_missing"
                else f"workspace model cost budget exhausted for {decision.currency}"
            )
            raise CostBudgetExceededError(message, decision)
        return decision

    def evaluate_budget(
        self,
        workspace_id: UUID,
        *,
        provider: str,
        model: str,
        now: datetime | None = None,
    ) -> CostBudgetDecision:
        now = ensure_aware_utc(now or datetime.now(UTC))
        normalized_provider = canonical_model_provider(provider)
        pricing = CostPricingService(self._session).resolve_rule(
            workspace_id=workspace_id,
            provider=normalized_provider,
            model=model,
            occurred_at=now,
        )
        if pricing is None:
            blocking_budget = self._session.scalar(
                select(WorkspaceCostBudget)
                .where(
                    WorkspaceCostBudget.workspace_id == workspace_id,
                    WorkspaceCostBudget.enabled.is_(True),
                    WorkspaceCostBudget.enforcement == "block",
                )
                .order_by(WorkspaceCostBudget.currency, WorkspaceCostBudget.id)
                .limit(1)
            )
            return CostBudgetDecision(
                allowed=blocking_budget is None,
                reason=(
                    "pricing_unconfigured"
                    if blocking_budget is None
                    else "pricing_rule_missing"
                ),
                evaluated_at=now,
                provider=normalized_provider,
                model=model,
                pricing_rule_id=None,
                pricing_version=None,
                currency=blocking_budget.currency if blocking_budget is not None else None,
                budget_id=blocking_budget.id if blocking_budget is not None else None,
                budget_state="unconfigured",
                enforcement=(
                    blocking_budget.enforcement if blocking_budget is not None else None
                ),
                spent=None,
                monthly_limit=(
                    blocking_budget.monthly_limit if blocking_budget is not None else None
                ),
                warning_ratio=(
                    blocking_budget.warning_ratio if blocking_budget is not None else None
                ),
                utilization_ratio=None,
            )
        budget = self._session.scalar(
            select(WorkspaceCostBudget).where(
                WorkspaceCostBudget.workspace_id == workspace_id,
                WorkspaceCostBudget.currency == pricing.currency,
                WorkspaceCostBudget.enabled.is_(True),
            )
        )
        if budget is None:
            return CostBudgetDecision(
                allowed=True,
                reason="budget_unconfigured",
                evaluated_at=now,
                provider=normalized_provider,
                model=model,
                pricing_rule_id=pricing.id,
                pricing_version=pricing.version,
                currency=pricing.currency,
                budget_id=None,
                budget_state="unconfigured",
                enforcement=None,
                spent=None,
                monthly_limit=None,
                warning_ratio=None,
                utilization_ratio=None,
            )
        status = CostQueryService(self._session).budget_status(
            workspace_id,
            currency=budget.currency,
            now=now,
        )
        blocked = status.state == "exhausted" and budget.enforcement == "block"
        return CostBudgetDecision(
            allowed=not blocked,
            reason="budget_exhausted" if blocked else f"budget_{status.state}",
            evaluated_at=now,
            provider=normalized_provider,
            model=model,
            pricing_rule_id=pricing.id,
            pricing_version=pricing.version,
            currency=pricing.currency,
            budget_id=budget.id,
            budget_state=status.state,
            enforcement=budget.enforcement,
            spent=status.spent,
            monthly_limit=status.monthly_limit,
            warning_ratio=status.warning_ratio,
            utilization_ratio=status.utilization_ratio,
        )

    def record_budget_blocked(
        self,
        *,
        run: AgentRun,
        request: AgentRunRequest,
        decision: CostBudgetDecision,
    ) -> None:
        AuditService(self._session).record_system_action(
            workspace_id=run.workspace_id,
            action="cost.budget_blocked",
            target_type="agent_run",
            target_id=run.id,
            metadata={
                "provider": request.provider,
                "model": request.model or request.agent_profile.model,
                "budget_decision": decision.snapshot(),
            },
        )
        GovernanceNotificationService(self._session).record_budget_blocked(
            workspace_id=run.workspace_id,
            run_id=run.id,
            decision=decision.snapshot(),
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


def _missing_usage() -> NormalizedModelUsage:
    return NormalizedModelUsage(
        request_count=1,
        input_tokens=0,
        output_tokens=0,
        cached_input_tokens=0,
        reasoning_tokens=0,
        total_tokens=0,
        raw_usage={},
        available=False,
    )


def _decimal_string(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
