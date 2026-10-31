"""Deployment-owned Kubernetes configuration, separate from user Run overrides."""

from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from opsmesh.framework.typing import OpsMeshContract

_NAME = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"


class KubernetesWorkerConfiguration(OpsMeshContract):
    api_url: str = "https://kubernetes.default.svc"
    namespace: str = Field(default="opsmesh", pattern=_NAME)
    deployment_id: str = Field(default="opsmesh", pattern=_NAME)
    token_file: Path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    ca_file: Path | None = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
    service_account: str = Field(default="opsmesh-run", pattern=_NAME)
    settings_secret: str = Field(default="opsmesh-run-settings", pattern=_NAME)
    environment_secrets: tuple[str, ...] = ()
    data_claim: str = Field(default="opsmesh-content", pattern=_NAME)
    image_pull_policy: Literal["Always", "IfNotPresent", "Never"] = "IfNotPresent"
    job_deadline_seconds: int = Field(default=3600, ge=30, le=172800)
    ttl_seconds: int = Field(default=3600, ge=60, le=604800)
    termination_grace_seconds: int = Field(default=30, ge=1, le=300)
    ephemeral_storage_mb: int = Field(default=512, ge=64)
    api_timeout_seconds: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)
    log_limit_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)

    @model_validator(mode="after")
    def validate_connection(self) -> Self:
        from re import fullmatch

        url = urlsplit(self.api_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("Kubernetes API requires an HTTPS origin without credentials")
        if any(not fullmatch(_NAME, name) for name in self.environment_secrets):
            raise ValueError("Kubernetes Secret names must be DNS labels")
        return self
