import pytest

from backend.app.core.config import Settings


def test_settings_defaults_are_local_development_friendly() -> None:
    settings = Settings()

    assert settings.environment == "local"
    assert settings.service_name == "chaincloud-backend"
    assert settings.api_prefix == "/api/v1"
    assert settings.enable_api_docs is True


def test_production_requires_real_token_and_disabled_docs() -> None:
    with pytest.raises(ValueError, match="CHAINCLOUD_INTERNAL_API_TOKEN"):
        Settings(environment="production")

    with pytest.raises(ValueError, match="CHAINCLOUD_ENABLE_API_DOCS"):
        Settings(environment="production", internal_api_token="secret")

    settings = Settings(
        environment="production",
        internal_api_token="secret",
        enable_api_docs=False,
    )
    assert settings.environment == "production"
