from __future__ import annotations

from backend.app.model_providers.metadata import budget_is_exhausted
from backend.app.model_providers.models import ModelProviderCredential

MODEL_PROVIDER_NOT_ACTIVE = "model_provider_not_active"
MODEL_PROVIDER_UNHEALTHY = "model_provider_unhealthy"
MODEL_PROVIDER_BUDGET_EXHAUSTED = "model_provider_budget_exhausted"


def credential_not_selectable_reasons(
    credential: ModelProviderCredential | None,
) -> list[str]:
    if credential is None:
        return ["model_provider_missing"]
    reasons: list[str] = []
    if credential.status != "active":
        reasons.append(MODEL_PROVIDER_NOT_ACTIVE)
    if credential.health_status == "unhealthy":
        reasons.append(MODEL_PROVIDER_UNHEALTHY)
    if budget_is_exhausted(credential.budget_metadata):
        reasons.append(MODEL_PROVIDER_BUDGET_EXHAUSTED)
    return reasons


def credential_is_selectable(credential: ModelProviderCredential | None) -> bool:
    if credential is None:
        return False
    return not credential_not_selectable_reasons(credential)
