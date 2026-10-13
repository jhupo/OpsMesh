from __future__ import annotations

from opsmesh.agents.providers.classification import is_openai_compatible_provider
from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.shared.security.egress import EgressURLPolicy, url_host, validate_egress_url

MODEL_PROVIDER_NOT_ACTIVE = "model_provider_not_active"
MODEL_PROVIDER_UNHEALTHY = "model_provider_unhealthy"
MODEL_PROVIDER_BUDGET_EXHAUSTED = "model_provider_budget_exhausted"


def credential_not_selectable_reasons(
    credential: ModelProviderCredential | None,
) -> list[str]:
    from opsmesh.agents.providers.metadata import budget_is_exhausted

    if credential is None:
        return ["model_provider_missing"]
    reasons: list[str] = []
    if not is_openai_compatible_provider(credential.provider):
        reasons.append("model_provider_unsupported")
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


def model_provider_base_url_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    return url_host(base_url)


def validated_base_url(
    base_url: str | None,
    *,
    egress_policy: EgressURLPolicy,
) -> str | None:
    if base_url is None:
        return None
    return validate_egress_url(base_url, policy=egress_policy)
