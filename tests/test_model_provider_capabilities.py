from opsmesh.agents.providers.capabilities import (
    ModelCapability,
    list_model_capabilities,
    resolve_model_capability,
)


def catalog():
    return [
        ModelCapability(
            provider=provider,
            model=model,
            display_name=model,
            supports_tools=True,
            supports_vision=False,
            supports_json_mode=True,
            supports_streaming=True,
        ).model_dump()
        for provider, model in (
            ("anthropic", "claude-company-model"),
            ("openai-compatible", "provider/custom-model"),
        )
    ]


def test_model_capability_catalog_is_scoped_by_provider_and_capability():
    tools = list_model_capabilities(catalog(), provider="anthropic", capability="tools")
    assert len(tools) == 1
    assert tools[0].model == "claude-company-model"
    assert list_model_capabilities(catalog(), capability="vision") == []


def test_model_capability_resolution_requires_an_explicit_catalog_entry():
    supported = resolve_model_capability("openai-compatible", "provider/custom-model", catalog())
    assert supported is not None and supported.supports_tools
    assert resolve_model_capability("anthropic", "unknown-model", catalog()) is None
    assert resolve_model_capability("anthropic", "provider/custom-model", catalog()) is None
