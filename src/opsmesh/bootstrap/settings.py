"""Deployment settings; credentials remain environment references."""

import json
import os
from pathlib import Path
from typing import Literal, Self

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract
from opsmesh.infrastructure.run_workers.kubernetes.configuration import (
    KubernetesWorkerConfiguration,
)


class OpsMeshSettings(OpsMeshContract):
    data_directory: Path = Path(".opsmesh")
    database_url: str | None = None
    redis_url: str | None = None
    plugins: tuple[str, ...] = ()
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    token_ttl_seconds: int = Field(default=3600, ge=60)
    worker_enabled: bool = True
    poll_interval_seconds: float = Field(default=0.5, gt=0)
    allow_trusted_local: bool = False
    tracing_enabled: bool = False
    tracing_endpoint: str | None = None
    tracing_headers_secret_ref: str | None = None
    tracing_timeout_seconds: float = Field(default=5, gt=0, le=60)
    tracing_queue_size: int = Field(default=2048, ge=1, le=65536)
    login_requests_per_minute: int = Field(default=10, gt=0)
    max_http_body_bytes: int = Field(default=2_000_000, gt=0)
    sse_heartbeat_seconds: float = Field(default=15, gt=0)
    budget_currency: str = "USD"
    run_reservation_amount: str = "0.10"
    run_worker_mode: Literal["inline", "subprocess", "docker", "kubernetes"] = "inline"
    kubernetes_worker: KubernetesWorkerConfiguration | None = None
    run_worker_image: str | None = None
    run_worker_cpu: float = Field(default=1, gt=0)
    run_worker_memory_mb: int = Field(default=512, ge=64)
    run_worker_pids_limit: int = Field(default=128, ge=1)
    run_worker_network: str = "bridge"
    run_worker_environment_allowlist: tuple[str, ...] = ()
    memory_extract_model_profile: str | None = None
    memory_consolidate_model_profile: str | None = None
    skill_git_allowed_hosts: tuple[str, ...] = ()
    personal_model_allowed_hosts: tuple[str, ...] = ("api.openai.com",)
    script_execution_provider: Literal["local", "docker"] = "docker"
    script_execution_image: str = "python:3.13-slim"
    storage_backend: Literal["local", "s3"] = "local"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region_name: str | None = None
    s3_prefix: str = "opsmesh/objects"
    s3_access_key_secret_ref: str | None = None
    s3_secret_key_secret_ref: str | None = None
    s3_session_token_secret_ref: str | None = None

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.data_directory.resolve() / 'opsmesh.db').as_posix()}"

    @classmethod
    def from_environment(cls) -> Self:
        values: dict[str, object] = {}
        for field in cls.model_fields:
            value = os.environ.get(f"OPSMESH_{field.upper()}")
            if value is not None:
                if field == "kubernetes_worker":
                    values[field] = json.loads(value)
                    continue
                values[field] = (
                    tuple(filter(None, value.split(",")))
                    if field
                    in {
                        "plugins",
                        "run_worker_environment_allowlist",
                        "skill_git_allowed_hosts",
                        "personal_model_allowed_hosts",
                    }
                    else value
                )
        return cls.model_validate(values)
