from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunRequest, AgentRunResult
from backend.app.observability.audit_service import AuditService
from backend.app.core.trace_context import current_trace_context
from backend.app.observability.cost_models import ModelPricingRule, ModelUsageRecord, WorkspaceCostBudget
from backend.app.observability.cost_usage import NormalizedModelUsage, normalize_model_usage
from backend.app.model_providers.provider_keys import canonical_model_provider
from backend.app.runs.models import AgentRun

_MILLION = Decimal(1_000_000)
_COST_QUANTUM = Decimal("0.000000000001")


class CostBudgetExceededError(RuntimeError):
    pass


@dataclass(frozen=True)
class CostBudgetStatus:
    state: str
    currency: str
    spent: Decimal
    monthly_limit: Decimal | None
    warning_ratio: Decimal | None
    utilization_ratio: Decimal | None
    enforcement: str | None
    unpriced_records: int
    period_start: datetime
    period_end: datetime


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

        occurred_at = _as_utc(occurred_at or datetime.now(UTC))
        provider = canonical_model_provider(request.provider or "openai")
        model = request.model or request.agent_profile.model
        usage = normalize_model_usage(result)
        pricing = self._pricing_rule(
            workspace_id=run.workspace_id,
            provider=provider,
            model=model,
            occurred_at=occurred_at,
        )
        costs = (
            _calculate_costs(usage, pricing) if pricing is not None and usage.available else None
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
        now = _as_utc(now or datetime.now(UTC))
        pricing = self._pricing_rule(
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
        status = self.budget_status(workspace_id, currency=budget.currency, now=now)
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
        normalized_currency = _currency(currency)
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
        status = self.budget_status(
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

    def create_pricing_rule(
        self,
        *,
        workspace_id: UUID,
        actor_user_id: UUID,
        provider: str,
        model: str,
        version: str,
        currency: str,
        input_rate_per_million: Decimal,
        output_rate_per_million: Decimal,
        cached_input_rate_per_million: Decimal | None,
        request_rate: Decimal,
        effective_from: datetime,
        effective_to: datetime | None,
        source: str,
    ) -> ModelPricingRule:
        normalized_provider = canonical_model_provider(provider)
        normalized_model = model.strip()
        normalized_version = version.strip()
        if not normalized_provider:
            raise ValueError("provider must not be blank")
        if not normalized_model:
            raise ValueError("model must not be blank")
        if not normalized_version:
            raise ValueError("version must not be blank")
        effective_from = _as_utc(effective_from)
        effective_to = _as_utc(effective_to) if effective_to is not None else None
        if effective_to is not None and effective_to <= effective_from:
            raise ValueError("effective_to must be later than effective_from")
        rule = ModelPricingRule(
            workspace_id=workspace_id,
            created_by_user_id=actor_user_id,
            provider=normalized_provider,
            model=normalized_model,
            version=normalized_version,
            currency=_currency(currency),
            input_rate_per_million=input_rate_per_million,
            output_rate_per_million=output_rate_per_million,
            cached_input_rate_per_million=cached_input_rate_per_million,
            request_rate=request_rate,
            effective_from=effective_from,
            effective_to=effective_to,
            status="active",
            source=source.strip() or "operator",
        )
        self._session.add(rule)
        self._session.flush([rule])
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="cost.pricing_rule_created",
            target_type="model_pricing_rule",
            target_id=rule.id,
            metadata={
                "provider": rule.provider,
                "model": rule.model,
                "version": rule.version,
                "currency": rule.currency,
                "source": rule.source,
            },
        )
        return rule

    def disable_pricing_rule(
        self,
        *,
        workspace_id: UUID,
        pricing_rule_id: UUID,
        actor_user_id: UUID,
    ) -> ModelPricingRule:
        rule = self._session.scalar(
            select(ModelPricingRule).where(
                ModelPricingRule.workspace_id == workspace_id,
                ModelPricingRule.id == pricing_rule_id,
            )
        )
        if rule is None:
            raise ValueError("Model pricing rule not found")
        rule.status = "disabled"
        self._session.flush([rule])
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action="cost.pricing_rule_disabled",
            target_type="model_pricing_rule",
            target_id=rule.id,
            metadata={"provider": rule.provider, "model": rule.model, "version": rule.version},
        )
        return rule

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
        normalized_currency = _currency(currency)
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

    def list_pricing_rules(self, workspace_id: UUID) -> list[ModelPricingRule]:
        return list(
            self._session.scalars(
                select(ModelPricingRule)
                .where(ModelPricingRule.workspace_id == workspace_id)
                .order_by(
                    ModelPricingRule.effective_from.desc(), ModelPricingRule.created_at.desc()
                )
            ).all()
        )

    def list_usage(
        self,
        workspace_id: UUID,
        *,
        start_at: datetime,
        end_at: datetime,
        provider: str | None,
        model: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ModelUsageRecord], int]:
        filters = [
            ModelUsageRecord.workspace_id == workspace_id,
            ModelUsageRecord.occurred_at >= start_at,
            ModelUsageRecord.occurred_at < end_at,
        ]
        if provider is not None:
            filters.append(ModelUsageRecord.provider == canonical_model_provider(provider))
        if model is not None:
            filters.append(ModelUsageRecord.model == model)
        total = int(
            self._session.scalar(select(func.count()).select_from(ModelUsageRecord).where(*filters))
            or 0
        )
        rows = self._session.scalars(
            select(ModelUsageRecord)
            .where(*filters)
            .order_by(ModelUsageRecord.occurred_at.desc(), ModelUsageRecord.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return list(rows), total

    def summary(
        self,
        workspace_id: UUID,
        *,
        start_at: datetime,
        end_at: datetime,
        currency: str,
        group_by: str,
    ) -> dict[str, object]:
        normalized_currency = _currency(currency)
        filters = (
            ModelUsageRecord.workspace_id == workspace_id,
            ModelUsageRecord.occurred_at >= start_at,
            ModelUsageRecord.occurred_at < end_at,
            or_(
                ModelUsageRecord.currency == normalized_currency,
                ModelUsageRecord.currency.is_(None),
            ),
        )
        aggregate_columns = _aggregate_columns(normalized_currency)
        totals_row = (
            self._session.execute(select(*aggregate_columns).where(*filters)).mappings().one()
        )
        group_expression = _group_expression(group_by)
        group_rows = self._session.execute(
            select(group_expression.label("key"), *aggregate_columns)
            .where(*filters)
            .group_by(group_expression)
            .order_by(group_expression)
        ).mappings()
        return {
            "start_at": start_at,
            "end_at": end_at,
            "currency": normalized_currency,
            "group_by": group_by,
            "totals": _totals_from_row(totals_row),
            "groups": [{"key": str(row["key"]), **_totals_from_row(row)} for row in group_rows],
            "budget": asdict(
                self.budget_status(
                    workspace_id,
                    currency=normalized_currency,
                    now=end_at,
                )
            ),
        }

    def budget_status(
        self,
        workspace_id: UUID,
        *,
        currency: str = "USD",
        now: datetime | None = None,
    ) -> CostBudgetStatus:
        now = now or datetime.now(UTC)
        period_start, period_end = _month_window(now)
        normalized_currency = _currency(currency)
        budget = self._session.scalar(
            select(WorkspaceCostBudget).where(
                WorkspaceCostBudget.workspace_id == workspace_id,
                WorkspaceCostBudget.currency == normalized_currency,
            )
        )
        spent = self._session.scalar(
            select(func.coalesce(func.sum(ModelUsageRecord.total_cost), 0)).where(
                ModelUsageRecord.workspace_id == workspace_id,
                ModelUsageRecord.currency == normalized_currency,
                ModelUsageRecord.occurred_at >= period_start,
                ModelUsageRecord.occurred_at < period_end,
            )
        )
        spent = Decimal(str(spent or 0))
        unpriced = int(
            self._session.scalar(
                select(func.count())
                .select_from(ModelUsageRecord)
                .where(
                    ModelUsageRecord.workspace_id == workspace_id,
                    ModelUsageRecord.metering_status != "priced",
                    or_(
                        ModelUsageRecord.currency == normalized_currency,
                        ModelUsageRecord.currency.is_(None),
                    ),
                    ModelUsageRecord.occurred_at >= period_start,
                    ModelUsageRecord.occurred_at < period_end,
                )
            )
            or 0
        )
        if budget is None or not budget.enabled:
            return CostBudgetStatus(
                state="unconfigured" if budget is None else "disabled",
                currency=normalized_currency,
                spent=spent,
                monthly_limit=None if budget is None else budget.monthly_limit,
                warning_ratio=None if budget is None else budget.warning_ratio,
                utilization_ratio=None,
                enforcement=None if budget is None else budget.enforcement,
                unpriced_records=unpriced,
                period_start=period_start,
                period_end=period_end,
            )
        ratio = spent / budget.monthly_limit
        state = "exhausted" if ratio >= 1 else "warning" if ratio >= budget.warning_ratio else "ok"
        return CostBudgetStatus(
            state=state,
            currency=normalized_currency,
            spent=spent,
            monthly_limit=budget.monthly_limit,
            warning_ratio=budget.warning_ratio,
            utilization_ratio=ratio,
            enforcement=budget.enforcement,
            unpriced_records=unpriced,
            period_start=period_start,
            period_end=period_end,
        )

    def _pricing_rule(
        self,
        *,
        workspace_id: UUID,
        provider: str,
        model: str,
        occurred_at: datetime,
    ) -> ModelPricingRule | None:
        exact_first = case((ModelPricingRule.model == model, 0), else_=1)
        return self._session.scalar(
            select(ModelPricingRule)
            .where(
                ModelPricingRule.workspace_id == workspace_id,
                ModelPricingRule.provider == provider,
                ModelPricingRule.model.in_([model, "*"]),
                ModelPricingRule.status == "active",
                ModelPricingRule.effective_from <= occurred_at,
                or_(
                    ModelPricingRule.effective_to.is_(None),
                    ModelPricingRule.effective_to > occurred_at,
                ),
            )
            .order_by(
                exact_first,
                ModelPricingRule.effective_from.desc(),
                ModelPricingRule.created_at.desc(),
                ModelPricingRule.id.desc(),
            )
            .limit(1)
        )


def _calculate_costs(
    usage: NormalizedModelUsage,
    pricing: ModelPricingRule,
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    cached_tokens = min(usage.cached_input_tokens, usage.input_tokens)
    regular_input_tokens = usage.input_tokens - cached_tokens
    cached_rate = pricing.cached_input_rate_per_million or pricing.input_rate_per_million
    input_cost = _cost(regular_input_tokens, pricing.input_rate_per_million)
    cached_cost = _cost(cached_tokens, cached_rate)
    output_cost = _cost(usage.output_tokens, pricing.output_rate_per_million)
    request_cost = (Decimal(usage.request_count) * pricing.request_rate).quantize(_COST_QUANTUM)
    total = (input_cost + cached_cost + output_cost + request_cost).quantize(_COST_QUANTUM)
    return input_cost, output_cost, cached_cost, request_cost, total


def _cost(tokens: int, rate_per_million: Decimal) -> Decimal:
    return (Decimal(tokens) * rate_per_million / _MILLION).quantize(_COST_QUANTUM)


def _currency(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
        raise ValueError("currency must be a three-letter ASCII code")
    return normalized


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _month_window(value: datetime) -> tuple[datetime, datetime]:
    current = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _aggregate_columns(currency: str) -> tuple[Any, ...]:
    priced = and_(
        ModelUsageRecord.metering_status == "priced",
        ModelUsageRecord.currency == currency,
    )
    return (
        func.count().label("records"),
        func.coalesce(func.sum(case((priced, 1), else_=0)), 0).label("priced_records"),
        func.coalesce(func.sum(case((priced, 0), else_=1)), 0).label("unpriced_records"),
        func.coalesce(func.sum(ModelUsageRecord.request_count), 0).label("request_count"),
        func.coalesce(func.sum(ModelUsageRecord.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(ModelUsageRecord.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(ModelUsageRecord.cached_input_tokens), 0).label(
            "cached_input_tokens"
        ),
        func.coalesce(func.sum(ModelUsageRecord.reasoning_tokens), 0).label("reasoning_tokens"),
        func.coalesce(func.sum(ModelUsageRecord.total_tokens), 0).label("total_tokens"),
        func.coalesce(
            func.sum(case((priced, ModelUsageRecord.total_cost), else_=Decimal("0"))),
            Decimal("0"),
        ).label("total_cost"),
    )


def _totals_from_row(row: Any) -> dict[str, int | Decimal]:
    return {
        "records": int(row["records"] or 0),
        "priced_records": int(row["priced_records"] or 0),
        "unpriced_records": int(row["unpriced_records"] or 0),
        "request_count": int(row["request_count"] or 0),
        "input_tokens": int(row["input_tokens"] or 0),
        "output_tokens": int(row["output_tokens"] or 0),
        "cached_input_tokens": int(row["cached_input_tokens"] or 0),
        "reasoning_tokens": int(row["reasoning_tokens"] or 0),
        "total_tokens": int(row["total_tokens"] or 0),
        "total_cost": Decimal(str(row["total_cost"] or 0)),
    }


def _group_expression(group_by: str) -> Any:
    if group_by == "provider":
        return ModelUsageRecord.provider
    if group_by == "model":
        return ModelUsageRecord.provider + "/" + ModelUsageRecord.model
    if group_by == "agent":
        return func.coalesce(cast(ModelUsageRecord.agent_profile_id, String), "unassigned")
    if group_by == "run":
        return cast(ModelUsageRecord.agent_run_id, String)
    if group_by == "day":
        return cast(func.date(ModelUsageRecord.occurred_at), String)
    raise ValueError("group_by must be provider, model, agent, run, or day")
