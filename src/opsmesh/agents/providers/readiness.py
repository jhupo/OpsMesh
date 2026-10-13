"""Provider availability policy shared by agent and team read models."""

from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.agents.providers.policy import credential_not_selectable_reasons


def provider_readiness(
    credential: ModelProviderCredential | None,
) -> tuple[list[str], list[str]]:
    if credential is None:
        return ["model_provider_unavailable"], []
    reasons = credential_not_selectable_reasons(credential)
    warnings = (
        [f"model_provider_{credential.health_status}"]
        if credential.health_status in {"degraded", "unknown"}
        else []
    )
    return reasons, warnings
