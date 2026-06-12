import asyncio

import pytest

from backend.app.core.config import Settings
from backend.app.core.executors import (
    blocking_executor_snapshot,
    get_blocking_executor,
    run_blocking,
    shutdown_blocking_executor,
)
from backend.app.core.resources import recommend_runtime_resources
from backend.app.db.session import create_database_engine, database_pool_snapshot
from backend.app.redis.client import create_redis_client, redis_pool_snapshot


def test_settings_defaults_are_local_development_friendly() -> None:
    settings = Settings()

    assert settings.environment == "local"
    assert settings.service_name == "opsmesh-backend"
    assert settings.api_prefix == "/api/v1"
    assert settings.enable_api_docs is True
    recommendation = recommend_runtime_resources()
    assert settings.database_pool_size == recommendation.database_pool_size
    assert settings.database_max_overflow == recommendation.database_max_overflow
    assert settings.database_statement_timeout_ms == 30_000
    assert settings.blocking_thread_pool_workers == recommendation.blocking_thread_pool_workers
    assert settings.redis_max_connections == recommendation.redis_max_connections
    assert settings.tracing_enabled is True
    assert settings.request_slow_log_threshold_ms == 1_000
    assert settings.worker_heartbeat_token is None
    assert settings.readiness_worker_check_enabled is False
    assert settings.readiness_worker_stale_after_seconds == 300
    assert settings.mcp_health_check_stale_after_seconds == 86_400
    assert settings.audit_event_retention_days is None
    assert settings.audit_event_worm_enabled is True
    assert settings.storage_backend == "local"
    assert settings.external_call_max_attempts == 2
    assert settings.external_call_circuit_failure_threshold == 5
    assert settings.external_call_circuit_reset_seconds == 60
    assert settings.secret_vault_providers == {}
    assert settings.release_dir is None
    assert settings.release_update_enabled is False
    assert settings.release_update_manifest_url is None
    assert settings.release_update_manifest_file is None
    assert settings.release_update_bundle_url is None
    assert settings.release_update_bundle_file is None
    assert settings.release_update_checksum_url is None
    assert settings.release_update_checksum_file is None
    assert settings.release_update_repository == "jhupo/OpsMesh"
    assert settings.release_update_check_cache_seconds == 1_200
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
    with pytest.raises(ValueError, match="OPSMESH_INTERNAL_API_TOKEN"):
        Settings(environment="production")

    with pytest.raises(ValueError, match="OPSMESH_TOKEN_HASH_PEPPER"):
        Settings(
            environment="production",
            internal_api_token="secret",
            platform_admin_token="admin-secret",
        )

    with pytest.raises(ValueError, match="OPSMESH_ENABLE_API_DOCS"):
        Settings(
            environment="production",
            internal_api_token="secret",
            platform_admin_token="admin-secret",
            token_hash_pepper="pepper",
        )

    with pytest.raises(ValueError, match="OPSMESH_CREDENTIAL_ENCRYPTION_SECRET"):
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
    with pytest.raises(ValueError, match="OPSMESH_PLATFORM_ADMIN_TOKEN"):
        Settings(
            environment="production",
            internal_api_token="secret",
            token_hash_pepper="pepper",
            enable_api_docs=False,
            credential_encryption_secret="credential-secret",
        )


def test_production_requires_worker_heartbeat_token() -> None:
    with pytest.raises(ValueError, match="OPSMESH_WORKER_HEARTBEAT_TOKEN"):
        _production_settings(worker_heartbeat_token=None)


def test_production_requires_worker_readiness_check() -> None:
    with pytest.raises(ValueError, match="OPSMESH_READINESS_WORKER_CHECK_ENABLED"):
        _production_settings(readiness_worker_check_enabled=False)


def test_production_rejects_unsafe_runtime_and_infrastructure_defaults() -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        _production_settings(
            database_url="postgresql+psycopg://opsmesh:opsmesh@localhost:5432/opsmesh"
        )

    with pytest.raises(ValueError, match="REDIS_URL"):
        _production_settings(redis_url="redis://localhost:6379/0")

    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _production_settings(cors_origins=[])

    with pytest.raises(ValueError, match="STORAGE_ROOT"):
        _production_settings(storage_root=".opsmesh-storage")


def test_settings_redacted_summary_hides_secrets() -> None:
    settings = Settings(
        database_url="postgresql+psycopg://user:pass@db.example.com:5432/app?ssl=true",
        redis_url="redis://:redis-secret@redis.example.com:6379/0",
        internal_api_token="secret",
        credential_encryption_secret="credential-secret",
        credential_encryption_key_id="current-key",
        credential_encryption_previous_secrets={
            "old-key": "old-credential-secret",
            "older-key": "older-credential-secret",
        },
        secret_vault_providers={
            "vault": {
                "url": "https://vault.example.test/v1/secret?token=secret",
                "token": "vault-secret",
                "namespace": "platform",
            }
        },
        cors_origins=["https://console.example.com"],
        feature_flags={"workspace_memory": True, "docker_runtimes": False},
    )

    summary = settings.redacted_summary()

    assert summary["database_url"] == "postgresql+psycopg://***:***@db.example.com:5432/app"
    assert summary["redis_url"] == "redis://***:***@redis.example.com:6379/0"
    serialized = str(summary)
    assert "credential-secret" not in serialized
    assert "old-credential-secret" not in serialized
    assert "older-credential-secret" not in serialized
    assert "redis-secret" not in serialized
    assert "vault-secret" not in serialized
    assert "token=secret" not in serialized
    assert summary["cors_origins_count"] == 1
    assert summary["audit_event_retention_days"] is None
    assert summary["audit_event_worm_enabled"] is True
    assert summary["storage_backend"] == "local"
    assert summary["mcp_health_check_stale_after_seconds"] == 86_400
    assert summary["external_call_max_attempts"] == 2
    assert summary["external_call_circuit_failure_threshold"] == 5
    assert summary["external_call_circuit_reset_seconds"] == 60
    assert summary["tracing_enabled"] is True
    assert summary["credential_encryption_key_id"] == "current-key"
    assert summary["credential_encryption_previous_key_ids"] == ["old-key", "older-key"]
    assert summary["secret_vault_providers"] == {
        "vault": {
            "url_configured": True,
            "url_host": "vault.example.test",
            "token": "[redacted]",
            "namespace": "platform",
        }
    }
    assert summary["enabled_feature_flags"] == ["workspace_memory"]


def test_s3_storage_requires_bucket() -> None:
    with pytest.raises(ValueError, match="OPSMESH_S3_BUCKET"):
        Settings(environment="test", storage_backend="s3")


def test_s3_storage_blank_optional_settings_are_unset() -> None:
    settings = Settings(
        environment="test",
        storage_backend="s3",
        s3_bucket=" opsmesh ",
        s3_endpoint_url=" ",
        s3_region=" ",
        s3_access_key_id=" ",
        s3_secret_access_key=" ",
        s3_session_token=" ",
        s3_prefix=" dev ",
    )

    assert settings.s3_bucket == "opsmesh"
    assert settings.s3_endpoint_url is None
    assert settings.s3_region is None
    assert settings.s3_access_key_id is None
    assert settings.s3_secret_access_key is None
    assert settings.s3_session_token is None
    assert settings.s3_prefix == "dev"


def test_release_update_blank_optional_settings_are_unset() -> None:
    settings = Settings(
        environment="test",
        release_dir=" ",
        release_update_manifest_url=" ",
        release_update_manifest_file=" ",
        release_update_bundle_url=" ",
        release_update_bundle_file=" ",
        release_update_checksum_url=" ",
        release_update_checksum_file=" ",
    )

    assert settings.release_dir is None
    assert settings.release_update_manifest_url is None
    assert settings.release_update_manifest_file is None
    assert settings.release_update_bundle_url is None
    assert settings.release_update_bundle_file is None
    assert settings.release_update_checksum_url is None
    assert settings.release_update_checksum_file is None


def test_s3_storage_redacted_summary_hides_credentials() -> None:
    settings = Settings(
        environment="test",
        storage_backend="s3",
        s3_bucket="opsmesh",
        s3_endpoint_url="https://access:secret@minio.example.com:9000",
        s3_region="us-east-1",
        s3_prefix="tenant-a",
        s3_access_key_id="access-key",
        s3_secret_access_key="secret-key",
        s3_session_token="session-token",
        s3_addressing_style="path",
    )

    summary = settings.redacted_summary()

    assert summary["storage_backend"] == "s3"
    assert summary["s3_bucket"] == "opsmesh"
    assert summary["s3_endpoint_url"] == "https://***:***@minio.example.com:9000"
    assert summary["s3_region"] == "us-east-1"
    assert summary["s3_prefix"] == "tenant-a"
    assert summary["s3_access_key_id_configured"] is True
    assert summary["s3_secret_access_key_configured"] is True
    assert summary["s3_session_token_configured"] is True
    assert summary["s3_addressing_style"] == "path"
    assert "secret-key" not in str(summary)
    assert "session-token" not in str(summary)


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
    snapshot = database_pool_snapshot(engine)
    assert snapshot.backend == "postgresql"
    assert snapshot.pool_size == 3
    assert snapshot.max_overflow == 4
    assert snapshot.checked_out == 0
    engine.dispose()


def test_database_engine_skips_queue_pool_settings_for_sqlite() -> None:
    settings = Settings(database_url="sqlite+pysqlite:///:memory:")

    engine = create_database_engine(settings)

    assert engine.url.get_backend_name() == "sqlite"
    snapshot = database_pool_snapshot(engine)
    assert snapshot.backend == "sqlite"
    assert snapshot.pool_class is not None
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
    snapshot = redis_pool_snapshot(client)
    assert snapshot.max_connections == 7
    assert snapshot.created_connections == 0
    assert snapshot.available_connections == 0
    assert snapshot.in_use_connections == 0
    client.close()
    client.connection_pool.disconnect()


def test_blocking_executor_reuses_configured_thread_pool() -> None:
    settings = Settings(blocking_thread_pool_workers=2)
    try:
        initial_snapshot = blocking_executor_snapshot(settings)
        first = get_blocking_executor(settings)
        second = get_blocking_executor(settings)
        result = asyncio.run(run_blocking(lambda value: value + 1, 41, settings=settings))
        running_snapshot = blocking_executor_snapshot(settings)

        assert initial_snapshot.initialized is False
        assert initial_snapshot.configured_workers == 2
        assert first is second
        assert first._max_workers == 2  # noqa: SLF001
        assert result == 42
        assert running_snapshot.initialized is True
        assert running_snapshot.configured_workers == 2
        assert running_snapshot.active_threads >= 1
        assert running_snapshot.queued_work_items >= 0
    finally:
        shutdown_blocking_executor(wait=True)


def _production_settings(**overrides: object) -> Settings:
    values = {
        "environment": "production",
        "internal_api_token": "secret",
        "platform_admin_token": "admin-secret",
        "worker_heartbeat_token": "worker-heartbeat-secret",
        "readiness_worker_check_enabled": True,
        "token_hash_pepper": "pepper",
        "enable_api_docs": False,
        "credential_encryption_secret": "credential-secret",
        "database_url": "postgresql+psycopg://app:strong@db.example.com:5432/app",
        "redis_url": "redis://redis.example.com:6379/0",
        "cors_origins": ["https://console.example.com"],
        "storage_root": "/srv/opsmesh/storage",
    }
    values.update(overrides)
    return Settings(**values)
