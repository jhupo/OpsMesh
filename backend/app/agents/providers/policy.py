from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.app.agents.providers.models import ModelProviderCredential
from backend.app.platform.security.egress import EgressUrlPolicy, validate_egress_url

MODEL_PROVIDER_NOT_ACTIVE = "model_provider_not_active"
MODEL_PROVIDER_UNHEALTHY = "model_provider_unhealthy"
MODEL_PROVIDER_BUDGET_EXHAUSTED = "model_provider_budget_exhausted"


def credential_not_selectable_reasons(
    credential: ModelProviderCredential | None,
) -> list[str]:
    from backend.app.agents.providers.metadata import budget_is_exhausted

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

def model_provider_base_url_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    return urlparse(base_url).netloc or None


def normalize_openai_compatible_base_url(base_url: str | None) -> str | None:
    if base_url is None:
        return None
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return base_url
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        normalized_path = path
    elif path == "":
        normalized_path = "/v1"
    else:
        normalized_path = f"{path}/v1"
    return urlunparse((parsed.scheme, parsed.netloc, normalized_path, "", "", ""))

class ModelProviderDefaultService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def unset_other_defaults(
        self,
        workspace_id: UUID,
        credential_id: UUID | None = None,
    ) -> None:
        statement = update(ModelProviderCredential).where(
            ModelProviderCredential.workspace_id == workspace_id
        )
        if credential_id is not None:
            statement = statement.where(ModelProviderCredential.id != credential_id)
        self._session.execute(statement.values(is_default=False))

OPENAI_COMPATIBLE_PROVIDERS = {"openai", "openai-compatible"}
ANTHROPIC_PROVIDERS = {"anthropic"}

_PROVIDER_ALIASES = {
    "openai": "openai",
    "openai-compatible": "openai-compatible",
    "anthropic": "anthropic",
}


def model_provider_key(provider: str | None) -> str:
    key = re.sub(r"[^a-z0-9]+", "-", (provider or "").strip().lower())
    return key.strip("-")


def canonical_model_provider(provider: str | None) -> str:
    key = model_provider_key(provider)
    return _PROVIDER_ALIASES.get(key, key)


def is_openai_compatible_provider(provider: str | None) -> bool:
    return canonical_model_provider(provider) in OPENAI_COMPATIBLE_PROVIDERS


def is_anthropic_provider(provider: str | None) -> bool:
    return canonical_model_provider(provider) in ANTHROPIC_PROVIDERS

def validated_base_url(
    base_url: str | None,
    *,
    provider: str | None,
    egress_policy: EgressUrlPolicy,
) -> str | None:
    if base_url is None:
        return None
    validated = validate_egress_url(base_url, policy=egress_policy)
    if is_openai_compatible_provider(provider):
        return normalize_openai_compatible_base_url(validated)
    return validated.rstrip("/")
