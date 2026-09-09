from backend.app.model_providers.base_url import (
    model_provider_base_url_host,
    normalize_openai_compatible_base_url,
)


def test_normalize_openai_compatible_base_url_adds_v1_to_root_url() -> None:
    assert (
        normalize_openai_compatible_base_url("https://dash.ovload.com/")
        == "https://dash.ovload.com/v1"
    )


def test_normalize_openai_compatible_base_url_preserves_existing_v1() -> None:
    assert (
        normalize_openai_compatible_base_url("https://dash.ovload.com/v1")
        == "https://dash.ovload.com/v1"
    )


def test_normalize_openai_compatible_base_url_appends_v1_to_provider_path() -> None:
    assert (
        normalize_openai_compatible_base_url("https://router.example.test/openai")
        == "https://router.example.test/openai/v1"
    )


def test_normalize_openai_compatible_base_url_drops_query_and_fragment() -> None:
    assert (
        normalize_openai_compatible_base_url("https://dash.ovload.com/?token=secret#frag")
        == "https://dash.ovload.com/v1"
    )


def test_normalize_openai_compatible_base_url_allows_none() -> None:
    assert normalize_openai_compatible_base_url(None) is None


def test_model_provider_base_url_host_returns_only_authority() -> None:
    assert model_provider_base_url_host("https://llm.example.test:8443/v1/private") == (
        "llm.example.test:8443"
    )
    assert model_provider_base_url_host("not-a-url/private") is None
