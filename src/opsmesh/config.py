"""Process-level configuration: addresses, connection pools, listeners, key references."""

import os
from dataclasses import dataclass
from functools import lru_cache
from ipaddress import ip_network
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.security.url_validation import validate_url_shape

LogFormat = Literal["json", "text"]
POSTGRES_PSYCOPG_DRIVER = "postgresql+psycopg"


@dataclass(frozen=True)
class ConnectionPoolRecommendation:
    cpu_count: int
    database_pool_size: int
    database_max_overflow: int
    redis_max_connections: int

    def as_dict(self) -> dict[str, int]:
        return {
            "cpu_count": self.cpu_count,
            "database_pool_size": self.database_pool_size,
            "database_max_overflow": self.database_max_overflow,
            "redis_max_connections": self.redis_max_connections,
        }


def recommend_connection_pools(cpu_count: int | None = None) -> ConnectionPoolRecommendation:
    cpus = max(1, int(cpu_count or os.cpu_count() or 1))
    database_pool_size = min(max(cpus * 2, 5), 32)
    return ConnectionPoolRecommendation(
        cpu_count=cpus,
        database_pool_size=database_pool_size,
        database_max_overflow=min(max(database_pool_size, 5), 64),
        redis_max_connections=min(max(database_pool_size * 3, 16), 128),
    )


_RESOURCE_RECOMMENDATION = recommend_connection_pools()


class Settings(BaseSettings):
    build_commit: str | None = Field(default=None, min_length=1, max_length=200)
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="OPSMESH_",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Process
    environment: str = Field(default="local")
    service_name: str = Field(default="opsmesh-backend")

    # HTTP
    api_prefix: str = Field(default="/api/v1")
    enable_api_docs: bool = Field(default=True)
    cors_origins: list[str] = Field(default_factory=list)
    request_slow_log_threshold_ms: int = Field(default=1_000, ge=1)
    api_rate_limit_enabled: bool = Field(default=False)
    api_rate_limit_requests: int = Field(default=600, ge=1)
    api_rate_limit_window_seconds: int = Field(default=60, ge=1)
    auth_rate_limit_requests: int = Field(default=20, ge=1)
    admin_rate_limit_requests: int = Field(default=120, ge=1)
    trusted_proxy_hops: int = Field(default=0, ge=0, le=10)
    trusted_proxy_networks: list[str] = Field(default_factory=list)

    # Logging and telemetry
    log_level: str = Field(default="INFO")
    log_format: LogFormat = Field(default="json")
    log_queue_capacity: int = Field(default=2048, ge=1, le=65536)
    metrics_host: str = "127.0.0.1"
    metrics_port: int | None = Field(default=None, ge=0, le=65535)
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

    # PostgreSQL
    database_url: str = Field(default="postgresql+psycopg://opsmesh:opsmesh@localhost:5432/opsmesh")
    database_pool_size: int = Field(default=_RESOURCE_RECOMMENDATION.database_pool_size, ge=1)
    database_max_overflow: int = Field(default=_RESOURCE_RECOMMENDATION.database_max_overflow, ge=0)
    database_pool_timeout_seconds: int = Field(default=30, ge=1)
    database_pool_recycle_seconds: int = Field(default=1_800, ge=30)
    database_statement_timeout_ms: int = Field(default=30_000, ge=1_000)

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_max_connections: int = Field(
        default=_RESOURCE_RECOMMENDATION.redis_max_connections,
        ge=1,
    )
    redis_socket_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_socket_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    redis_health_check_interval_seconds: int = Field(default=30, ge=0)
    redis_key_prefix: str = Field(default="opsmesh")

    # Temporal
    temporal_address: str = Field(default="localhost:7233", min_length=1)
    temporal_namespace: str = Field(default="default", min_length=1)
    temporal_task_queue: str = Field(default="opsmesh", min_length=1)
    temporal_worker_identity: str | None = Field(default=None, min_length=1, max_length=256)
    temporal_tls_enabled: bool = Field(default=False)
    temporal_api_key: str | None = Field(default=None)
    temporal_workflow_concurrency: int = Field(default=128, ge=1)
    temporal_activity_concurrency: int = Field(default=64, ge=1)
    temporal_dispatch_concurrency: int = Field(default=16, ge=1)
    temporal_outbox_database_pool_size: int = Field(default=4, ge=1)
    temporal_shutdown_grace_seconds: int = Field(default=120, ge=1)

    # Credential encryption referenced by dispatch sealing and owner secrets
    credential_encryption_key: str = Field(default="JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=")
    credential_encryption_key_id: str = Field(default="local")
    credential_encryption_previous_keys: dict[str, str] = Field(default_factory=dict)

    # Resource content root; owners decide their own layout below it
    storage_root: str = Field(default=".opsmesh-storage")
    plugins_root: Path | None = None
    agent_catalog_path: Path | None = None

    # Users
    session_ttl_seconds: int = Field(default=30 * 24 * 60 * 60, ge=60)
    invitation_ttl_seconds: int = Field(default=7 * 24 * 60 * 60, ge=3_600)
    platform_base_url: str = Field(default="")
    default_user_token_limit: int | None = Field(default=None, ge=0)

    # Per-user runtime capacity; a null limit means unlimited
    default_user_concurrency_limit: int | None = Field(default=None, ge=0)
    default_user_environment_limit: int | None = Field(default=None, ge=0)
    execution_sandbox_images: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        SecretCipher(
            key=self.credential_encryption_key,
            key_id=self.credential_encryption_key_id,
            previous_keys=self.credential_encryption_previous_keys,
        )
        self.trusted_proxy_networks = [
            str(ip_network(network, strict=True)) for network in self.trusted_proxy_networks
        ]
        if self.trusted_proxy_hops and not self.trusted_proxy_networks:
            raise ValueError("Trusted proxy hops require explicit trusted proxy networks")
        return self

    @model_validator(mode="after")
    def validate_telemetry_configuration(self) -> "Settings":
        for field_name in ("otel_exporter_otlp_endpoint", "temporal_api_key"):
            value = getattr(self, field_name)
            if value is not None:
                stripped = value.strip()
                setattr(self, field_name, stripped or None)
        endpoint = self.otel_exporter_otlp_endpoint
        if endpoint is not None:
            validate_url_shape(endpoint, allowed_schemes=frozenset({"http", "https"}))
        if self.otel_batch_max_export_size > self.otel_batch_max_queue_size:
            raise ValueError(
                "OPSMESH_OTEL_BATCH_MAX_EXPORT_SIZE must not exceed "
                "OPSMESH_OTEL_BATCH_MAX_QUEUE_SIZE"
            )
        if self.environment.lower() in {"production", "prod"}:
            if self.credential_encryption_key == "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=":
                raise ValueError("OPSMESH_CREDENTIAL_ENCRYPTION_KEY must be set in production")
            if "localhost" in self.database_url or "opsmesh:opsmesh" in self.database_url:
                raise ValueError("OPSMESH_DATABASE_URL must not use local default credentials")
            if self.redis_url == "redis://localhost:6379/0":
                raise ValueError("OPSMESH_REDIS_URL must not use the local default")
            if self.enable_api_docs:
                raise ValueError("OPSMESH_ENABLE_API_DOCS must be false in production")
            if not self.api_rate_limit_enabled:
                raise ValueError("OPSMESH_API_RATE_LIMIT_ENABLED must be true in production")
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
            "temporal_address": self.temporal_address,
            "temporal_namespace": self.temporal_namespace,
            "temporal_task_queue": self.temporal_task_queue,
            "temporal_worker_identity": self.temporal_worker_identity,
            "temporal_tls_enabled": self.temporal_tls_enabled,
            "temporal_api_key_configured": bool(self.temporal_api_key),
            "temporal_workflow_concurrency": self.temporal_workflow_concurrency,
            "temporal_activity_concurrency": self.temporal_activity_concurrency,
            "temporal_dispatch_concurrency": self.temporal_dispatch_concurrency,
            "temporal_outbox_database_pool_size": self.temporal_outbox_database_pool_size,
            "tracing_enabled": self.tracing_enabled,
            "otel_logs_enabled": self.otel_logs_enabled,
            "otel_exporter_otlp_endpoint": _redact_url(self.otel_exporter_otlp_endpoint)
            if self.otel_exporter_otlp_endpoint is not None
            else None,
            "otel_exporter_otlp_insecure": self.otel_exporter_otlp_insecure,
            "otel_exporter_otlp_header_names": sorted(self.otel_exporter_otlp_headers),
            "otel_trace_sample_ratio": self.otel_trace_sample_ratio,
            "api_rate_limit_enabled": self.api_rate_limit_enabled,
            "api_rate_limit_requests": self.api_rate_limit_requests,
            "api_rate_limit_window_seconds": self.api_rate_limit_window_seconds,
            "auth_rate_limit_requests": self.auth_rate_limit_requests,
            "admin_rate_limit_requests": self.admin_rate_limit_requests,
            "trusted_proxy_hops": self.trusted_proxy_hops,
            "trusted_proxy_networks": self.trusted_proxy_networks,
            "storage_root": self.storage_root,
            "session_ttl_seconds": self.session_ttl_seconds,
            "invitation_ttl_seconds": self.invitation_ttl_seconds,
            "platform_base_url_configured": bool(self.platform_base_url),
            "default_user_token_limit": self.default_user_token_limit,
            "default_user_concurrency_limit": self.default_user_concurrency_limit,
            "default_user_environment_limit": self.default_user_environment_limit,
            "credential_encryption_key_id": self.credential_encryption_key_id,
            "credential_encryption_previous_key_ids": sorted(
                self.credential_encryption_previous_keys
            ),
            "cors_origins_count": len(self.cors_origins),
        }


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return "<invalid-url>"
    if not parsed.netloc:
        return "<invalid-url>"
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    rendered_port = f":{port}" if port is not None else ""
    username = parsed.username
    redacted_auth = "***:***@" if username is not None else ""
    netloc = f"{redacted_auth}{hostname}{rendered_port}"
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
