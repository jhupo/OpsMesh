import asyncio

import pytest

from backend.app.core.config import Settings
from backend.app.core.executors import (
    get_blocking_executor,
    run_blocking,
    shutdown_blocking_executor,
)
from backend.app.core.resources import recommend_runtime_resources
from backend.app.db.session import create_database_engine
from backend.app.redis.client import create_redis_client


def test_settings_defaults_are_local_development_friendly() -> None:
    settings = Settings()

    assert settings.environment == "local"
    assert settings.service_name == "chaincloud-backend"
    assert settings.api_prefix == "/api/v1"
    assert settings.enable_api_docs is True
    recommendation = recommend_runtime_resources()
    assert settings.database_pool_size == recommendation.database_pool_size
    assert settings.database_max_overflow == recommendation.database_max_overflow
    assert settings.database_statement_timeout_ms == 30_000
    assert settings.blocking_thread_pool_workers == recommendation.blocking_thread_pool_workers
    assert settings.redis_max_connections == recommendation.redis_max_connections
    assert settings.request_slow_log_threshold_ms == 1_000
    assert settings.feature_flags == {}


def test_runtime_resource_recommendations_scale_with_cpu_count() -> None:
    small = recommend_runtime_resources(cpu_count=1)
    medium = recommend_runtime_resources(cpu_count=8)
    large = recommend_runtime_resources(cpu_count=128)

    assert small.database_pool_size == 5
    assert small.blocking_thread_pool_workers == 8
    assert small.worker_max_jobs == 1
    assert medium.database_pool_size == 16
    assert medium.blocking_thread_pool_workers == 32
    assert medium.worker_max_jobs == 8
    assert large.database_pool_size == 32
    assert large.blocking_thread_pool_workers == 64
    assert large.redis_max_connections == 96
    assert large.worker_max_jobs == 16


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

    settings = _production_settings()
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


def test_production_rejects_unsafe_runtime_and_infrastructure_defaults() -> None:
    with pytest.raises(ValueError, match="AGENT_RUNNER_BACKEND"):
        _production_settings(agent_runner_backend="fake")

    with pytest.raises(ValueError, match="DATABASE_URL"):
        _production_settings(
            database_url="postgresql+psycopg://chaincloud:chaincloud@localhost:5432/chaincloud"
        )

    with pytest.raises(ValueError, match="REDIS_URL"):
        _production_settings(redis_url="redis://localhost:6379/0")

    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _production_settings(cors_origins=[])

    with pytest.raises(ValueError, match="STORAGE_ROOT"):
        _production_settings(storage_root=".chaincloud-storage")


def test_settings_redacted_summary_hides_secrets() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://user:pass@db.example.com:5432/app?ssl=true",
        redis_url="redis://:redis-secret@redis.example.com:6379/0",
        internal_api_token="secret",
        credential_encryption_secret="credential-secret",
        cors_origins=["https://console.example.com"],
        feature_flags={"workspace_memory": True, "docker_runtimes": False},
    )

    summary = settings.redacted_summary()

    assert summary["database_url"] == "postgresql+psycopg://***:***@db.example.com:5432/app"
    assert summary["redis_url"] == "redis://***:***@redis.example.com:6379/0"
    assert "secret" not in str(summary)
    assert summary["cors_origins_count"] == 1
    assert summary["enabled_feature_flags"] == ["workspace_memory"]


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


def test_redis_client_uses_configured_connection_pool() -> None:
    settings = Settings(
        redis_url="redis://localhost:6379/1",
        redis_max_connections=7,
        redis_socket_timeout_seconds=1.5,
        redis_socket_connect_timeout_seconds=2.5,
        redis_health_check_interval_seconds=13,
    )

    client = create_redis_client(settings)

    assert client.connection_pool.max_connections == 7
    connection_kwargs = client.connection_pool.connection_kwargs
    assert connection_kwargs["socket_timeout"] == 1.5
    assert connection_kwargs["socket_connect_timeout"] == 2.5
    assert connection_kwargs["health_check_interval"] == 13
    client.close()
    client.connection_pool.disconnect()


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


def _production_settings(**overrides: object) -> Settings:
    values = {
        "environment": "production",
        "internal_api_token": "secret",
        "platform_admin_token": "admin-secret",
        "token_hash_pepper": "pepper",
        "enable_api_docs": False,
        "credential_encryption_secret": "credential-secret",
        "agent_runner_backend": "openai",
        "database_url": "postgresql+psycopg://app:strong@db.example.com:5432/app",
        "redis_url": "redis://redis.example.com:6379/0",
        "cors_origins": ["https://console.example.com"],
        "storage_root": "/srv/chaincloud/storage",
    }
    values.update(overrides)
    return Settings(**values)
