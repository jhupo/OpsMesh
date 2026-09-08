from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.core.resources import recommend_runtime_resources
from backend.app.secrets.service import redact_secret_provider_configs

LogFormat = Literal["json", "text"]
StorageBackend = Literal["local", "s3"]
_RESOURCE_RECOMMENDATION = recommend_runtime_resources()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="OPSMESH_",
        extra="ignore",
    )

    environment: str = Field(default="local")
    service_name: str = Field(default="opsmesh-backend")
    api_prefix: str = Field(default="/api/v1")
    log_level: str = Field(default="INFO")
    log_format: LogFormat = Field(default="json")
    enable_api_docs: bool = Field(default=True)
    cors_origins: list[str] = Field(default_factory=list)
    database_url: str = Field(
        default="postgresql+psycopg://opsmesh:opsmesh@localhost:5432/opsmesh"
    )
    database_pool_size: int = Field(default=_RESOURCE_RECOMMENDATION.database_pool_size, ge=1)
    database_max_overflow: int = Field(default=_RESOURCE_RECOMMENDATION.database_max_overflow, ge=0)
    database_pool_timeout_seconds: int = Field(default=30, ge=1)
    database_pool_recycle_seconds: int = Field(default=1_800, ge=30)
    database_statement_timeout_ms: int = Field(default=30_000, ge=1_000)
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_max_connections: int = Field(
        default=_RESOURCE_RECOMMENDATION.redis_max_connections,
        ge=1,
    )
    redis_socket_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_socket_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_health_check_interval_seconds: int = Field(default=30, ge=0)
    redis_key_prefix: str = Field(default="opsmesh")
    worker_queue_name: str = Field(default="agent_runs")
    readiness_worker_check_enabled: bool = Field(default=False)
    readiness_worker_stale_after_seconds: int = Field(default=300, ge=60)
    mcp_health_check_stale_after_seconds: int = Field(default=24 * 60 * 60, ge=1)
    blocking_thread_pool_workers: int = Field(
        default=_RESOURCE_RECOMMENDATION.blocking_thread_pool_workers,
        ge=1,
    )
    tracing_enabled: bool = Field(default=True)
    otel_logs_enabled: bool = Field(default=True)
    otel_exporter_otlp_endpoint: str | None = Field(default=None)
    otel_exporter_otlp_insecure: bool = Field(default=False)
    otel_exporter_otlp_headers: dict[str, str] = Field(default_factory=dict)
    otel_trace_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)
    otel_export_timeout_seconds: int = Field(default=10, ge=1, le=60)
    otel_batch_max_queue_size: int = Field(default=2_048, ge=1)
    otel_batch_max_export_size: int = Field(default=512, ge=1)
    otel_batch_schedule_delay_ms: int = Field(default=5_000, ge=100, le=60_000)
    otel_excluded_urls: str = Field(
        default="/api/v1/health,/api/v1/health/live,/api/v1/metrics"
    )
    request_slow_log_threshold_ms: int = Field(default=1_000, ge=1)
    internal_api_token: str = Field(default="change-me-in-production")
    platform_admin_token: str | None = Field(default=None)
    worker_heartbeat_token: str | None = Field(default=None)
    token_hash_pepper: str = Field(default="change-me-token-pepper")
    audit_event_retention_days: int | None = Field(default=None, ge=1)
    audit_event_worm_enabled: bool = Field(default=True)
    audit_integrity_check_interval_seconds: int = Field(default=3_600, ge=60)
    audit_integrity_stale_after_seconds: int = Field(default=7_200, ge=60)
    agent_tool_approval_timeout_seconds: int = Field(default=86_400, ge=60)
    storage_backend: StorageBackend = Field(default="local")
    storage_root: str = Field(default=".opsmesh-storage")
    s3_bucket: str = Field(default="")
    s3_endpoint_url: str | None = Field(default=None)
    s3_region: str | None = Field(default=None)
    s3_prefix: str = Field(default="")
    s3_access_key_id: str | None = Field(default=None)
    s3_secret_access_key: str | None = Field(default=None)
    s3_session_token: str | None = Field(default=None)
    s3_use_ssl: bool = Field(default=True)
    s3_addressing_style: Literal["auto", "virtual", "path"] = Field(default="auto")
    max_upload_bytes: int = Field(default=10 * 1024 * 1024)
    agent_file_read_max_bytes: int = Field(default=1024 * 1024, ge=1)
    agent_file_read_content_types: list[str] = Field(
        default_factory=lambda: [
            "application/json",
            "application/toml",
            "application/x-yaml",
            "application/xml",
            "text/csv",
            "text/markdown",
            "text/plain",
            "text/tab-separated-values",
            "text/x-python",
            "text/xml",
            "text/yaml",
        ]
    )
    external_call_max_attempts: int = Field(default=2, ge=1, le=5)
    external_call_circuit_failure_threshold: int = Field(default=5, ge=1, le=100)
    external_call_circuit_reset_seconds: int = Field(default=60, ge=1, le=3_600)
    api_rate_limit_enabled: bool = Field(default=False)
    api_rate_limit_requests: int = Field(default=600, ge=1)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1)
    auth_rate_limit_requests: int = Field(default=20, ge=1)
    admin_rate_limit_requests: int = Field(default=120, ge=1)
    trusted_proxy_hops: int = Field(default=0, ge=0, le=10)
    credential_encryption_secret: str = Field(default="change-me-credential-encryption-secret")
    credential_encryption_key_id: str = Field(default="local")
    credential_encryption_previous_secrets: dict[str, str] = Field(default_factory=dict)
    secret_vault_providers: dict[str, dict[str, object]] = Field(default_factory=dict)
    runtime_allowed_images: list[str] = Field(default_factory=lambda: ["opsmesh-runtime:local"])
    release_dir: str | None = Field(default=None)
    release_update_enabled: bool = Field(default=False)
    release_update_script: str = Field(default="/opt/opsmesh/current/scripts/server-update.sh")
    release_update_timeout_seconds: int = Field(default=900, ge=30, le=7_200)
    release_update_manifest_url: str | None = Field(default=None)
    release_update_manifest_file: str | None = Field(default=None)
    release_update_bundle_url: str | None = Field(default=None)
    release_update_bundle_file: str | None = Field(default=None)
    release_update_checksum_url: str | None = Field(default=None)
    release_update_checksum_file: str | None = Field(default=None)
    release_update_repository: str = Field(default="jhupo/OpsMesh")
    release_update_check_cache_seconds: int = Field(default=1_200, ge=0, le=86_400)
    release_update_github_api_url: str = Field(default="https://api.github.com")
    release_update_http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    feature_flags: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        self.s3_bucket = self.s3_bucket.strip()
        self.s3_prefix = self.s3_prefix.strip()
        for field_name in (
            "s3_endpoint_url",
            "s3_region",
            "s3_access_key_id",
            "s3_secret_access_key",
            "s3_session_token",
            "release_dir",
            "release_update_manifest_url",
            "release_update_manifest_file",
            "release_update_bundle_url",
            "release_update_bundle_file",
            "release_update_checksum_url",
            "release_update_checksum_file",
            "otel_exporter_otlp_endpoint",
        ):
            value = getattr(self, field_name)
            if value is not None:
                stripped = value.strip()
                setattr(self, field_name, stripped or None)
        if self.storage_backend == "s3" and not self.s3_bucket.strip():
            raise ValueError("OPSMESH_S3_BUCKET must be set when OPSMESH_STORAGE_BACKEND=s3")
        if self.otel_batch_max_export_size > self.otel_batch_max_queue_size:
            raise ValueError(
                "OPSMESH_OTEL_BATCH_MAX_EXPORT_SIZE must not exceed "
                "OPSMESH_OTEL_BATCH_MAX_QUEUE_SIZE"
            )
        if self.environment.lower() in {"production", "prod"}:
            if self.internal_api_token == "change-me-in-production":
                raise ValueError("OPSMESH_INTERNAL_API_TOKEN must be set in production")
            if not self.platform_admin_token:
                raise ValueError("OPSMESH_PLATFORM_ADMIN_TOKEN must be set in production")
            if self.token_hash_pepper == "change-me-token-pepper":
                raise ValueError("OPSMESH_TOKEN_HASH_PEPPER must be set in production")
            if self.enable_api_docs:
                raise ValueError("OPSMESH_ENABLE_API_DOCS must be false in production")
            if not self.api_rate_limit_enabled:
                raise ValueError("OPSMESH_API_RATE_LIMIT_ENABLED must be true in production")
            if self.credential_encryption_secret == "change-me-credential-encryption-secret":
                raise ValueError(
                    "OPSMESH_CREDENTIAL_ENCRYPTION_SECRET must be set in production"
                )
            if not self.worker_heartbeat_token or not self.worker_heartbeat_token.strip():
                raise ValueError("OPSMESH_WORKER_HEARTBEAT_TOKEN must be set in production")
            if not self.readiness_worker_check_enabled:
                raise ValueError(
                    "OPSMESH_READINESS_WORKER_CHECK_ENABLED must be true in production"
                )
            if "localhost" in self.database_url or "opsmesh:opsmesh" in self.database_url:
                raise ValueError("OPSMESH_DATABASE_URL must not use local default credentials")
            if self.redis_url == "redis://localhost:6379/0":
                raise ValueError("OPSMESH_REDIS_URL must not use the local default")
            if not self.cors_origins:
                raise ValueError("OPSMESH_CORS_ORIGINS must be set in production")
            if self.storage_root == ".opsmesh-storage":
                raise ValueError("OPSMESH_STORAGE_ROOT must be explicit in production")
            if self.log_format != "json":
                raise ValueError("OPSMESH_LOG_FORMAT must be json in production")
            if (
                self.tracing_enabled or self.otel_logs_enabled
            ) and self.otel_exporter_otlp_endpoint is None:
                raise ValueError(
                    "OPSMESH_OTEL_EXPORTER_OTLP_ENDPOINT must be set when OTLP logs or "
                    "tracing are enabled in production"
                )
            if self.otel_exporter_otlp_insecure and not _is_loopback_endpoint(
                self.otel_exporter_otlp_endpoint
            ):
                raise ValueError(
                    "insecure OTLP export is allowed only to a loopback collector in production"
                )
        return self

    @property
    def internal_api_tokens(self) -> tuple[str, ...]:
        return tuple(
            token.strip()
            for token in self.internal_api_token.split(",")
            if token.strip()
        )

    def redacted_summary(self) -> dict[str, object]:
        return {
            "environment": self.environment,
            "service_name": self.service_name,
            "api_prefix": self.api_prefix,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "enable_api_docs": self.enable_api_docs,
            "database_url": _redact_url(self.database_url),
            "database_pool_size": self.database_pool_size,
            "database_max_overflow": self.database_max_overflow,
            "redis_url": _redact_url(self.redis_url),
            "redis_max_connections": self.redis_max_connections,
            "worker_queue_name": self.worker_queue_name,
            "readiness_worker_check_enabled": self.readiness_worker_check_enabled,
            "readiness_worker_stale_after_seconds": self.readiness_worker_stale_after_seconds,
            "mcp_health_check_stale_after_seconds": (
                self.mcp_health_check_stale_after_seconds
            ),
            "blocking_thread_pool_workers": self.blocking_thread_pool_workers,
            "tracing_enabled": self.tracing_enabled,
            "otel_logs_enabled": self.otel_logs_enabled,
            "otel_exporter_otlp_endpoint": _redact_url(self.otel_exporter_otlp_endpoint)
            if self.otel_exporter_otlp_endpoint is not None
            else None,
            "otel_exporter_otlp_insecure": self.otel_exporter_otlp_insecure,
            "otel_exporter_otlp_header_names": sorted(self.otel_exporter_otlp_headers),
            "otel_trace_sample_ratio": self.otel_trace_sample_ratio,
            "external_call_max_attempts": self.external_call_max_attempts,
            "external_call_circuit_failure_threshold": (
                self.external_call_circuit_failure_threshold
            ),
            "external_call_circuit_reset_seconds": self.external_call_circuit_reset_seconds,
            "api_rate_limit_enabled": self.api_rate_limit_enabled,
            "api_rate_limit_requests": self.api_rate_limit_requests,
            "api_rate_limit_window_seconds": self.api_rate_limit_window_seconds,
            "auth_rate_limit_requests": self.auth_rate_limit_requests,
            "admin_rate_limit_requests": self.admin_rate_limit_requests,
            "trusted_proxy_hops": self.trusted_proxy_hops,
            "audit_event_retention_days": self.audit_event_retention_days,
            "audit_event_worm_enabled": self.audit_event_worm_enabled,
            "audit_integrity_check_interval_seconds": (
                self.audit_integrity_check_interval_seconds
            ),
            "audit_integrity_stale_after_seconds": self.audit_integrity_stale_after_seconds,
            "storage_backend": self.storage_backend,
            "storage_root": self.storage_root,
            "agent_file_read_max_bytes": self.agent_file_read_max_bytes,
            "agent_file_read_content_types": sorted(self.agent_file_read_content_types),
            "s3_bucket": self.s3_bucket if self.storage_backend == "s3" else "",
            "s3_endpoint_url": _redact_url(self.s3_endpoint_url)
            if self.s3_endpoint_url is not None
            else None,
            "s3_region": self.s3_region,
            "s3_prefix": self.s3_prefix,
            "s3_access_key_id_configured": bool(self.s3_access_key_id),
            "s3_secret_access_key_configured": bool(self.s3_secret_access_key),
            "s3_session_token_configured": bool(self.s3_session_token),
            "s3_use_ssl": self.s3_use_ssl,
            "s3_addressing_style": self.s3_addressing_style,
            "secret_vault_providers": redact_secret_provider_configs(
                self.secret_vault_providers
            ),
            "release_dir": self.release_dir,
            "release_update_enabled": self.release_update_enabled,
            "release_update_script": self.release_update_script,
            "release_update_timeout_seconds": self.release_update_timeout_seconds,
            "release_update_manifest_url": self.release_update_manifest_url,
            "release_update_manifest_file": self.release_update_manifest_file,
            "release_update_bundle_url": self.release_update_bundle_url,
            "release_update_bundle_file": self.release_update_bundle_file,
            "release_update_checksum_url": self.release_update_checksum_url,
            "release_update_checksum_file": self.release_update_checksum_file,
            "release_update_repository": self.release_update_repository,
            "release_update_check_cache_seconds": self.release_update_check_cache_seconds,
            "release_update_github_api_url": self.release_update_github_api_url,
            "credential_encryption_key_id": self.credential_encryption_key_id,
            "credential_encryption_previous_key_ids": sorted(
                self.credential_encryption_previous_secrets
            ),
            "worker_heartbeat_token_configured": bool(self.worker_heartbeat_token),
            "cors_origins_count": len(self.cors_origins),
            "enabled_feature_flags": sorted(
                key for key, value in self.feature_flags.items() if value
            ),
        }


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    if not parsed.netloc:
        return value
    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    username = parsed.username
    redacted_auth = "***:***@" if username is not None else ""
    netloc = f"{redacted_auth}{hostname}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _is_loopback_endpoint(value: str | None) -> bool:
    if value is None:
        return False
    try:
        hostname = urlsplit(value).hostname
    except ValueError:
        return False
    return hostname in {"127.0.0.1", "::1", "localhost"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
