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

    with pytest.raises(ValueError, match="CHAINCLOUD_TOKEN_HASH_PEPPER"):
        Settings(
            environment="production",
            internal_api_token="secret",
            platform_admin_token="admin-secret",
        )

    with pytest.raises(ValueError, match="CHAINCLOUD_ENABLE_API_DOCS"):
        Settings(
            environment="production",
            internal_api_token="secret",
            platform_admin_token="admin-secret",
            token_hash_pepper="pepper",
        )

    with pytest.raises(ValueError, match="CHAINCLOUD_CREDENTIAL_ENCRYPTION_SECRET"):
        Settings(
            environment="production",
            internal_api_token="secret",
            platform_admin_token="admin-secret",
            token_hash_pepper="pepper",
            enable_api_docs=False,
        )

    settings = Settings(
        environment="production",
        internal_api_token="secret",
        platform_admin_token="admin-secret",
        token_hash_pepper="pepper",
        enable_api_docs=False,
        credential_encryption_secret="credential-secret",
    )
    assert settings.environment == "production"


def test_internal_api_token_supports_rotation_list() -> None:
    settings = Settings(internal_api_token="old-token, new-token ,,")

    assert settings.internal_api_tokens == ("old-token", "new-token")


def test_production_requires_platform_admin_token() -> None:
    with pytest.raises(ValueError, match="CHAINCLOUD_PLATFORM_ADMIN_TOKEN"):
        Settings(
            environment="production",
            internal_api_token="secret",
            token_hash_pepper="pepper",
            enable_api_docs=False,
            credential_encryption_secret="credential-secret",
        )
