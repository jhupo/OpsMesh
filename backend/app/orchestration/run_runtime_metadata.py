from typing import Any

from backend.app.agents.models import AgentProfile
from backend.app.core.trace_context import current_trace_metadata
from backend.app.orchestration.run_request_authorization import RunAuthorizationService
from backend.app.orchestration.run_request_context import RunRequestContextProvider
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task


class RunRuntimeMetadataBuilder:
    def __init__(
        self,
        authorization: RunAuthorizationService,
        context_provider: RunRequestContextProvider,
    ) -> None:
        self._authorization = authorization
        self._context_provider = context_provider

    def build(
        self,
        *,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
        model_provider: dict[str, Any],
        authorization_snapshot: dict[str, object],
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "agent_profile_id": str(profile.id) if profile.id is not None else None,
            "agent_role": profile.role,
            "run_model": model_provider["model"],
            "model_provider_provider": model_provider["provider"],
            "model_provider_credential_id": str(model_provider["model_provider_credential_id"])
            if model_provider["model_provider_credential_id"] is not None
            else None,
            "model_provider_model_api": model_provider["model_api"],
            "authorization_scope": "workspace",
            "authorized_workspace_id": str(run.workspace_id),
            "authorized_task_id": str(task.id) if task is not None else None,
            "tool_policy_source": "agent_profile",
            "authorization_snapshot_version": authorization_snapshot.get("version"),
            "authorization_snapshot_fingerprint": authorization_snapshot.get("fingerprint"),
            "capability_catalog_fingerprint": _catalog_fingerprint(authorization_snapshot),
            "runtime_binding": _runtime_binding_metadata(authorization_snapshot),
        }
        metadata.update(self._authorization.step_context_for_run(run))
        metadata.update(self._context_provider.mailbox_context_for_run(run, task, profile))
        metadata.update(self._context_provider.team_context_for_run(run, task, profile))
        metadata.update(current_trace_metadata())
        return metadata


def _catalog_fingerprint(snapshot: dict[str, object]) -> str | None:
    catalog = snapshot.get("capability_catalog")
    if not isinstance(catalog, dict):
        return None
    fingerprint = catalog.get("fingerprint")
    return fingerprint if isinstance(fingerprint, str) else None


def _runtime_binding_metadata(snapshot: dict[str, object]) -> dict[str, object]:
    binding = snapshot.get("runtime_binding")
    if not isinstance(binding, dict):
        return {}
    return {
        key: binding.get(key)
        for key in (
            "mode",
            "workspace_runtime_id",
            "runtime_space_id",
            "capability_resource_ids",
            "network_disabled",
            "file_access_scope",
        )
    }
