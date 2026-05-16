from backend.app.core.config import Settings


def test_settings_defaults_are_local_development_friendly() -> None:
    settings = Settings()

    assert settings.environment == "local"
    assert settings.service_name == "chaincloud-backend"
    assert settings.api_prefix == "/api/v1"
    assert settings.enable_api_docs is True

