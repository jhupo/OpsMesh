import pytest

from opsmesh.agents.providers.policy import model_provider_base_url_host, validated_base_url
from opsmesh.shared.security.egress import EgressUrlPolicy


@pytest.mark.parametrize(
    "url",
    [
        "https://gateway.example.test/",
        "https://gateway.example.test/v1",
        "https://gateway.example.test/custom/openai",
    ],
)
def test_provider_preserves_explicit_endpoint(url: str) -> None:
    assert (
        validated_base_url(
            url,
            provider="openai-compatible",
            egress_policy=EgressUrlPolicy(allowed_schemes=("https",)),
        )
        == url
    )


def test_model_provider_base_url_host_returns_only_authority() -> None:
    assert model_provider_base_url_host("https://llm.example.test:8443/v1/private") == (
        "llm.example.test:8443"
    )
    assert model_provider_base_url_host("not-a-url/private") is None
