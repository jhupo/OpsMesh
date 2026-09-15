"""Pricing-rule management and deterministic model-cost calculation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from backend.app.core.utils import ensure_aware_utc
from backend.app.domains.agents.providers.policy import canonical_model_provider
from backend.app.observability.audit.service import AuditService
from backend.app.observability.costs.models import ModelPricingRule
from backend.app.observability.costs.usage import NormalizedModelUsage

_MILLION = Decimal(1_000_000)
_COST_QUANTUM = Decimal("0.000000000001")


class CostPricingService:
    def __init__(self, session: Session) -> None:
        self._session = session

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
        effective_from = ensure_aware_utc(effective_from)
        effective_to = ensure_aware_utc(effective_to) if effective_to is not None else None
        if effective_to is not None and effective_to <= effective_from:
            raise ValueError("effective_to must be later than effective_from")
        rule = ModelPricingRule(
            workspace_id=workspace_id,
            created_by_user_id=actor_user_id,
            provider=normalized_provider,
            model=normalized_model,
            version=normalized_version,
            currency=normalize_currency(currency),
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

    def resolve_rule(
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


def calculate_costs(
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

def normalize_currency(value: str) -> str:
    normalized = value.strip().upper()
    if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
        raise ValueError("currency must be a three-letter ASCII code")
    return normalized
