from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.capabilities.mcp_adapter_payloads import (
    MCP_PYTHON_SDK_PACKAGE,
    MCP_PYTHON_SDK_STDIO_ENTRYPOINT,
    MCP_STDIO_CONTRACT_VERSION,
)
from backend.app.core.config import Settings
from backend.app.core.typing import string_list
from backend.app.runtimes.models import WorkspaceRuntime
from backend.app.self_hosted.models import RuntimeCredential, SelfHostedWorker
from backend.app.self_hosted.policy import positive_policy_int
from backend.app.workspaces.models import Workspace


@dataclass(frozen=True)
class WorkerTrustSnapshot:
    worker: SelfHostedWorker
    runtime: WorkspaceRuntime
    credential: RuntimeCredential | None
    trust_state: str
    policy_summary: dict[str, object]
    policy_diagnostics: list[dict[str, object]]


class SelfHostedTrustService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def list_worker_trust(self, workspace_id: UUID) -> list[WorkerTrustSnapshot]:
        version_policy = self._workspace_self_hosted_version_policy(workspace_id)
        workers = self._session.scalars(
            select(SelfHostedWorker)
            .where(SelfHostedWorker.workspace_id == workspace_id)
            .order_by(SelfHostedWorker.updated_at.desc(), SelfHostedWorker.name)
        ).all()
        snapshots: list[WorkerTrustSnapshot] = []
        for worker in workers:
            runtime = self._session.get(WorkspaceRuntime, worker.workspace_runtime_id)
            if runtime is None or runtime.workspace_id != workspace_id:
                continue
            credential = self._session.scalar(
                select(RuntimeCredential)
                .where(RuntimeCredential.workspace_runtime_id == runtime.id)
                .order_by(RuntimeCredential.created_at.desc())
                .limit(1)
            )
            snapshots.append(
                WorkerTrustSnapshot(
                    worker=worker,
                    runtime=runtime,
                    credential=credential,
                    trust_state=_worker_trust_state(worker, runtime, credential),
                    policy_summary=_worker_policy_summary(worker.capabilities),
                    policy_diagnostics=_worker_policy_diagnostics(
                        worker,
                        runtime,
                        version_policy=version_policy,
                    ),
                )
            )
        return snapshots

    def connector_manifest(self, workspace_id: UUID) -> dict[str, object]:
        version_policy = self._workspace_self_hosted_version_policy(workspace_id)
        return {
            "workspace_id": str(workspace_id),
            "api_prefix": self._settings.api_prefix,
            "connector": {
                "name": "opsmesh-self-hosted-worker",
                "protocol_version": 2,
                "recommended_version": _version_string(
                    version_policy.get("recommended_version")
                ),
                "min_version": _version_string(version_policy.get("min_version")),
                "upgrade_url": _version_string(version_policy.get("upgrade_url")),
            },
            "endpoints": {
                "register": f"{self._settings.api_prefix}/self-hosted/register",
                "heartbeat": f"{self._settings.api_prefix}/self-hosted/heartbeat",
                "next_job": f"{self._settings.api_prefix}/self-hosted/jobs/next",
                "claim_job": f"{self._settings.api_prefix}/self-hosted/jobs/{{agent_run_id}}/claim",
                "complete_job": (
                    f"{self._settings.api_prefix}/self-hosted/jobs/{{agent_run_id}}/complete"
                ),
                "project_archive": (
                    f"{self._settings.api_prefix}/self-hosted/jobs/{{agent_run_id}}/"
                    "project/archive"
                ),
                "project_output": (
                    f"{self._settings.api_prefix}/self-hosted/jobs/{{agent_run_id}}/project/"
                    "outputs/{project_output_id}"
                ),
                "next_mcp_job": f"{self._settings.api_prefix}/self-hosted/mcp-jobs/next",
                "claim_mcp_job": (
                    f"{self._settings.api_prefix}/self-hosted/mcp-jobs/{{mcp_job_id}}/claim"
                ),
                "complete_mcp_job": (
                    f"{self._settings.api_prefix}/self-hosted/mcp-jobs/{{mcp_job_id}}/complete"
                ),
                "progress": f"{self._settings.api_prefix}/self-hosted/progress",
                "local_files": f"{self._settings.api_prefix}/self-hosted/local-files",
                "artifact_uploads": f"{self._settings.api_prefix}/self-hosted/artifact-uploads",
            },
            "capability_contract": {
                "required": ["machine_id", "name", "version"],
                "optional_capabilities": [
                    "runtime_space_id",
                    "allowed_runtime_space_ids",
                    "allowed_tools",
                    "supported_models",
                    "supported_runtimes",
                    "supported_network_modes",
                    "max_concurrent_jobs",
                    "max_concurrent_mcp_jobs",
                    "max_artifact_bytes",
                ],
                "mcp_stdio": {
                    "contract_version": MCP_STDIO_CONTRACT_VERSION,
                    "transport": "stdio",
                    "sdk_package": MCP_PYTHON_SDK_PACKAGE,
                    "sdk_version": "1.27.1",
                    "sdk_entrypoint": MCP_PYTHON_SDK_STDIO_ENTRYPOINT,
                    "request_field": "request",
                },
                "project_files": {
                    "archive_format": "tar",
                    "manifest_path": ".opsmesh/project.json",
                    "download_after_claim": True,
                    "declared_outputs_only": True,
                    "completion_requires_required_outputs": True,
                },
            },
            "security": {
                "enrollment_token_transport": "one_time_registration_payload",
                "runtime_credential_transport": "authorization_bearer_token",
                "returns_credentials": False,
                "workspace_scoped": True,
            },
            "version_policy": {
                key: value
                for key, value in version_policy.items()
                if key in {"min_version", "recommended_version", "upgrade_url"}
            },
        }

    def _workspace_self_hosted_version_policy(self, workspace_id: UUID) -> dict[str, object]:
        workspace = self._session.get(Workspace, workspace_id)
        settings = workspace.settings if workspace is not None else None
        if not isinstance(settings, dict):
            return {}
        for key in (
            "self_hosted_worker_policy",
            "self_hosted_connector_policy",
            "self_hosted",
        ):
            policy = settings.get(key)
            if not isinstance(policy, dict):
                continue
            version_policy = policy.get("version_policy")
            if isinstance(version_policy, dict):
                return version_policy
            if "min_version" in policy or "recommended_version" in policy:
                return policy
        return {}


def _worker_trust_state(
    worker: SelfHostedWorker,
    runtime: WorkspaceRuntime,
    credential: RuntimeCredential | None,
) -> str:
    if credential is not None and credential.status == "revoked":
        return "revoked"
    if worker.status == "revoked" or runtime.status == "revoked":
        return "revoked"
    if worker.status == "quarantined" or runtime.status == "quarantined":
        return "quarantined"
    if worker.status == "degraded" or runtime.connection_status == "degraded":
        return "degraded"
    if worker.status in {"offline", "disabled"} or runtime.connection_status == "offline":
        return "offline"
    return "active"


def _worker_policy_summary(capabilities: dict[str, object]) -> dict[str, object]:
    return {
        "allowed_tools": string_list(capabilities.get("allowed_tools")),
        "supported_models": string_list(capabilities.get("supported_models")),
        "supported_runtimes": string_list(capabilities.get("supported_runtimes")),
        "supported_network_modes": string_list(capabilities.get("supported_network_modes")),
        "allowed_runtime_space_ids": string_list(capabilities.get("allowed_runtime_space_ids")),
        "max_concurrent_jobs": positive_policy_int(capabilities.get("max_concurrent_jobs")),
        "max_concurrent_mcp_jobs": positive_policy_int(capabilities.get("max_concurrent_mcp_jobs")),
        "max_artifact_bytes": positive_policy_int(capabilities.get("max_artifact_bytes")),
    }


def _worker_policy_diagnostics(
    worker: SelfHostedWorker,
    runtime: WorkspaceRuntime,
    *,
    version_policy: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    worker_policy = _worker_policy_summary(worker.capabilities)
    runtime_policy = _worker_policy_summary(runtime.capabilities)
    if worker_policy != runtime_policy:
        diagnostics.append(
            {
                "code": "policy_summary_mismatch",
                "severity": "warning",
                "message": "Worker and runtime policy summaries differ.",
                "worker_policy": worker_policy,
                "runtime_policy": runtime_policy,
            }
        )
    worker_runtime_space_id = _uuid_from_capabilities(worker.capabilities, "runtime_space_id")
    if (
        worker_runtime_space_id is not None
        and runtime.runtime_space_id is not None
        and worker_runtime_space_id != runtime.runtime_space_id
    ):
        diagnostics.append(
            {
                "code": "runtime_space_mismatch",
                "severity": "critical",
                "message": "Worker capability runtime space does not match runtime space.",
                "worker_runtime_space_id": str(worker_runtime_space_id),
                "runtime_space_id": str(runtime.runtime_space_id),
            }
        )
    if worker.status != "revoked" and runtime.status == "revoked":
        diagnostics.append(
            {
                "code": "runtime_revoked_worker_not_revoked",
                "severity": "critical",
                "message": "Runtime is revoked but worker record is not revoked.",
            }
        )
    diagnostics.extend(_worker_version_diagnostics(worker, version_policy or {}))
    return diagnostics


def _worker_version_diagnostics(
    worker: SelfHostedWorker,
    version_policy: dict[str, object],
) -> list[dict[str, object]]:
    current = _parse_version(worker.version)
    if current is None:
        return []
    min_version = _version_string(version_policy.get("min_version"))
    recommended_version = _version_string(version_policy.get("recommended_version"))
    upgrade_url = _version_string(version_policy.get("upgrade_url"))
    diagnostics: list[dict[str, object]] = []
    if min_version is not None:
        parsed_min = _parse_version(min_version)
        if parsed_min is not None and _version_less_than(current, parsed_min):
            diagnostics.append(
                {
                    "code": "self_hosted_connector_upgrade_required",
                    "severity": "critical",
                    "message": "Self-hosted connector version is below minimum supported version.",
                    "current_version": worker.version,
                    "min_version": min_version,
                    "recommended_version": recommended_version,
                    "upgrade_url": upgrade_url,
                }
            )
            return diagnostics
    if recommended_version is not None:
        parsed_recommended = _parse_version(recommended_version)
        if parsed_recommended is not None and _version_less_than(current, parsed_recommended):
            diagnostics.append(
                {
                    "code": "self_hosted_connector_upgrade_recommended",
                    "severity": "warning",
                    "message": "Self-hosted connector version is below recommended version.",
                    "current_version": worker.version,
                    "recommended_version": recommended_version,
                    "upgrade_url": upgrade_url,
                }
            )
    return diagnostics




def _uuid_from_capabilities(capabilities: dict[str, object], key: str) -> UUID | None:
    value = capabilities.get(key)
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _parse_version(value: object) -> tuple[int, ...] | None:
    text = _version_string(value)
    if text is None:
        return None
    normalized = text.removeprefix("v").replace("-", ".")
    parts: list[int] = []
    for raw_part in normalized.split("."):
        digits = "".join(char for char in raw_part if char.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def _version_less_than(current: tuple[int, ...], required: tuple[int, ...]) -> bool:
    length = max(len(current), len(required))
    padded_current = current + (0,) * (length - len(current))
    padded_required = required + (0,) * (length - len(required))
    return padded_current < padded_required


def _version_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
