from backend.app.model_providers.capabilities import (
    list_model_capabilities,
    resolve_model_capability,
)


def test_model_capability_registry_filters_by_provider_and_capability() -> None:
    anthropic_tools = list_model_capabilities(provider="Claude API", capability="tools")

    assert anthropic_tools
    assert {item.provider for item in anthropic_tools} == {"anthropic"}
    assert all("tools" in item.capabilities for item in anthropic_tools)


def test_model_capability_registry_resolves_wildcard_provider_model() -> None:
    capability = resolve_model_capability(
        "OpenAI Compatible Gateway",
        "provider/custom-model",
    )

    assert capability is not None
    assert capability.model == "*"
    assert capability.supports_tools is True
    assert capability.notes is not None


def test_model_capability_registry_resolves_anthropic_wildcard_aliases() -> None:
    capability = resolve_model_capability(
        "Claude API",
        "claude-custom-company-model",
    )

    assert capability is not None
    assert capability.provider == "anthropic"
    assert capability.model == "*"
    assert capability.supports_tools is True
    assert capability.supports_streaming is True
