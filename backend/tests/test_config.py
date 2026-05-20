import asyncio

import pytest

from backend.app.core.config import Settings
from backend.app.core.executors import (
    get_blocking_executor,
    run_blocking,
    shutdown_blocking_executor,
)
from backend.app.db.session import create_database_engine


def test_settings_defaults_are_local_development_friendly() -> None:
    settings = Settings()

    assert settings.environment == "local"
    assert settings.service_name == "chaincloud-backend"
    assert settings.api_prefix == "/api/v1"
    assert settings.enable_api_docs is True
    assert settings.database_pool_size == 10
    assert settings.database_max_overflow == 20
    assert settings.database_statement_timeout_ms == 30_000
    assert settings.blocking_thread_pool_workers == 16
    assert settings.request_slow_log_threshold_ms == 1_000


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


def test_database_engine_uses_pool_settings_for_postgres_url() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://user:pass@localhost:5432/app",
        database_pool_size=3,
        database_max_overflow=4,
        database_pool_timeout_seconds=5,
        database_pool_recycle_seconds=120,
        database_statement_timeout_ms=12_345,
    )

    engine = create_database_engine(settings)

    assert engine.pool.size() == 3
    assert engine.pool._max_overflow == 4  # noqa: SLF001
    assert engine.url.get_backend_name() == "postgresql"
    engine.dispose()


def test_database_engine_skips_queue_pool_settings_for_sqlite() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")

    engine = create_database_engine(settings)

    assert engine.url.get_backend_name() == "sqlite"
    engine.dispose()


def test_blocking_executor_reuses_configured_thread_pool() -> None:
    settings = Settings(blocking_thread_pool_workers=2)
    try:
        first = get_blocking_executor(settings)
        second = get_blocking_executor(settings)
        result = asyncio.run(run_blocking(lambda value: value + 1, 41, settings=settings))

        assert first is second
        assert first._max_workers == 2  # noqa: SLF001
        assert result == 42
    finally:
        shutdown_blocking_executor(wait=True)
