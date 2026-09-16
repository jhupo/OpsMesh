from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.core.utils import (
    dict_or_empty,
    optional_string,
    positive_int_or_default,
    uuid_or_none,
)
from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.agents.providers.metadata import budget_is_exhausted
from backend.app.domains.agents.providers.model_api import (
    canonical_model_api,
    model_api_for_agent_provider,
)
from backend.app.domains.agents.providers.models import ModelProviderCredential
from backend.app.domains.agents.providers.snapshots import ModelProviderResolutionService
from backend.app.domains.agents.runtime.guardrails import runtime_controls_snapshot
from backend.app.domains.capabilities.catalog.effective import (
    EffectiveCapabilityCatalogService,
    effective_catalog_fingerprint,
)
from backend.app.domains.orchestration.runs.authorization.runtime import (
    ResolvedRunRuntimeBinding,
    RunRuntimeAuthorizationService,
)
from backend.app.domains.orchestration.runs.authorization.tools import (
    AgentToolAuthorizationSnapshotService,
)
from backend.app.domains.orchestration.runs.models import (
    AUTHORIZATION_SNAPSHOT_VERSION,
    authorization_snapshot_fingerprint,
)
from backend.app.domains.orchestration.tasks.models import Task, TaskStep
from backend.app.domains.orchestration.workflows.planning.agent_plan import (
    is_agent_planning_step,
    planner_output_schema,
)
from backend.app.domains.workspace.tenants.models import Workspace

if TYPE_CHECKING:
    from backend.app.domains.orchestration.requests.builder import RunRequestBuilder


@dataclass(slots=True)
class RunAuthorizationSnapshotService:
    session: Session
    request_builder: RunRequestBuilder

    def build_authorization_snapshot(
        self,
        task: Task,
        step: TaskStep | None,
        profile: AgentProfile | None,
        *,
        agent_snapshot: dict[str, object] | None = None,
    ) -> dict[str, object]:
        catalog_snapshot, allowed_tools = self._capability_snapshot(task, step, profile)
        model_provider = self.model_provider_snapshot(
            task.workspace_id,
            profile,
            agent_snapshot=agent_snapshot,
        )
        runtime_profile = agent_runtime_profile_snapshot(
            workspace_id=task.workspace_id,
            profile=profile,
            agent_snapshot=agent_snapshot,
        )
        fallback_policy = self._model_provider_fallback_snapshot(task.workspace_id)
        runtime_policy = profile.runtime_policy if profile is not None else {}
        frozen_runtime_policy = runtime_policy_snapshot(runtime_policy)
        runtime_controls = self._runtime_controls(profile, step, frozen_runtime_policy)
        runtime_binding = RunRuntimeAuthorizationService(self.session).resolve_for_snapshot(
            task=task,
            step=step,
            capability_catalog=catalog_snapshot,
            runtime_policy=frozen_runtime_policy,
        )
        agent_tools = self._agent_tools(
            task,
            step,
            profile,
            catalog_snapshot,
            model_provider,
            runtime_binding.allowed_file_ids,
        )
        snapshot = self._snapshot_payload(
            task=task,
            step=step,
            profile=profile,
            runtime_profile=runtime_profile,
            fallback_policy=fallback_policy,
            catalog_snapshot=catalog_snapshot,
            allowed_tools=allowed_tools,
            model_provider=model_provider,
            frozen_runtime_policy=frozen_runtime_policy,
            runtime_controls=runtime_controls,
            runtime_binding=runtime_binding,
            agent_tools=agent_tools,
        )
        snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
        return snapshot

    def _capability_snapshot(
        self,
        task: Task,
        step: TaskStep | None,
        profile: AgentProfile | None,
    ) -> tuple[dict[str, object] | None, list[str]]:
        if profile is None:
            return None, []
        effective_catalog = EffectiveCapabilityCatalogService(self.session).resolve(
            workspace_id=task.workspace_id,
            agent_profile_id=profile.id,
            team_id=task.agent_team_id,
        )
        if is_agent_planning_step(step):
            effective_catalog.tools = []
            effective_catalog.fingerprint = effective_catalog_fingerprint(effective_catalog)
        return (
            effective_catalog.model_dump(mode="json"),
            [item.descriptor.name for item in effective_catalog.tools],
        )

    @staticmethod
    def _runtime_controls(
        profile: AgentProfile | None,
        step: TaskStep | None,
        frozen_runtime_policy: dict[str, object],
    ) -> dict[str, object]:
        controls = runtime_controls_snapshot(
            model_settings=profile.model_settings if profile is not None else {},
            runtime_policy=frozen_runtime_policy,
        )
        if is_agent_planning_step(step):
            controls["output_schema"] = asdict(planner_output_schema())
        return controls

    def _agent_tools(
        self,
        task: Task,
        step: TaskStep | None,
        profile: AgentProfile | None,
        catalog_snapshot: dict[str, object] | None,
        model_provider: dict[str, object],
        file_scope_ids: tuple[UUID, ...],
    ) -> list[dict[str, object]]:
        if is_agent_planning_step(step):
            return []
        return AgentToolAuthorizationSnapshotService(self.session).build(
            task=task,
            source_profile=profile,
            source_catalog=catalog_snapshot,
            source_model_provider=model_provider,
            file_scope_ids=file_scope_ids,
            model_provider_snapshot=lambda target: self.model_provider_snapshot(
                task.workspace_id,
                target,
            ),
        )

    def _snapshot_payload(
        self,
        *,
        task: Task,
        step: TaskStep | None,
        profile: AgentProfile | None,
        runtime_profile: dict[str, object],
        fallback_policy: dict[str, object] | None,
        catalog_snapshot: dict[str, object] | None,
        allowed_tools: list[str],
        model_provider: dict[str, object],
        frozen_runtime_policy: dict[str, object],
        runtime_controls: dict[str, object],
        runtime_binding: ResolvedRunRuntimeBinding,
        agent_tools: list[dict[str, object]],
    ) -> dict[str, object]:
        tool_policy = profile.tool_policy if profile is not None else {}
        memory_policy = profile.memory_policy if profile is not None else {}
        approval_policy = profile.approval_policy if profile is not None else {}
        return {
            "version": AUTHORIZATION_SNAPSHOT_VERSION,
            "workspace_id": str(task.workspace_id),
            "task_id": str(task.id),
            "task_step_id": str(step.id) if step is not None else None,
            "runtime_space_id": str(runtime_binding.runtime_space_id)
            if runtime_binding.runtime_space_id is not None
            else None,
            "agent_profile_id": str(profile.id)
            if profile is not None and profile.id is not None
            else None,
            "agent_profile": runtime_profile,
            "task_owner_agent_profile_id": str(task.owner_agent_profile_id)
            if task.owner_agent_profile_id is not None
            else None,
            "task_owner_version": max(int(task.owner_version or 1), 1),
            "allowed_tools": allowed_tools,
            "capability_catalog": catalog_snapshot,
            "tool_policy": dict_or_empty(tool_policy),
            "installed_skills": self.request_builder.installed_skill_snapshots(
                task.workspace_id,
                profile,
            ),
            "model_provider": model_provider,
            "model_provider_fallback": fallback_policy,
            "runtime_policy": frozen_runtime_policy,
            "memory_policy": dict_or_empty(memory_policy),
            "approval_policy": dict_or_empty(approval_policy),
            "output_schema": runtime_controls["output_schema"],
            "guardrails": runtime_controls["guardrails"],
            "agent_tools": agent_tools,
            "file_scope": {
                "mode": "authorized_file_resources",
                "workspace_id": str(task.workspace_id),
                "task_id": str(task.id),
                "allowed_file_ids": [str(file_id) for file_id in runtime_binding.allowed_file_ids],
            },
            "runtime_binding": runtime_binding.as_snapshot(),
        }

    def model_provider_snapshot(
        self,
        workspace_id: UUID,
        profile: AgentProfile | None,
        *,
        agent_snapshot: dict[str, object] | None = None,
    ) -> dict[str, object]:
        if agent_snapshot is not None:
            agent_model = optional_string(agent_snapshot.get("model"))
            if agent_model is None:
                raise ValueError("Team agent snapshot model is invalid")
            raw_credential_id = agent_snapshot.get("model_provider_credential_id")
            credential_id = uuid_or_none(raw_credential_id)
            if raw_credential_id is not None and credential_id is None:
                raise ValueError("Team agent snapshot model credential is invalid")
        else:
            agent_model = profile.model if profile is not None else "gpt-4.1"
            credential_id = None
        if credential_id is None and agent_snapshot is None and profile is not None:
            credential_id = profile.model_provider_credential_id
        agent_model_settings = (
            dict_or_empty(agent_snapshot.get("model_settings"))
            if agent_snapshot is not None
            else profile.model_settings
            if profile is not None
            else None
        )
        snapshot = (
            ModelProviderResolutionService(self.session)
            .resolve_snapshot_for_agent(
                workspace_id=workspace_id,
                agent_credential_id=credential_id,
                agent_model=agent_model,
            )
            .as_dict()
        )
        snapshot["model_api"] = model_api_for_agent_provider(
            optional_string(snapshot.get("provider")),
            agent_model_settings,
            {"model_api": snapshot.get("model_api")},
        )
        return snapshot

    def _model_provider_fallback_snapshot(
        self,
        workspace_id: UUID,
    ) -> dict[str, object] | None:
        workspace = self.session.get(Workspace, workspace_id)
        if workspace is None:
            raise ValueError("Workspace not found while freezing model provider fallback")
        settings = workspace.settings if isinstance(workspace.settings, dict) else {}
        raw_policy = settings.get("model_provider_fallback")
        if not isinstance(raw_policy, dict) or raw_policy.get("enabled") is not True:
            return None
        retry_codes = raw_policy.get("retry_error_codes")
        frozen: dict[str, object] = {
            "enabled": True,
            "retry_error_codes": [
                item for item in retry_codes if isinstance(item, str) and item
            ]
            if isinstance(retry_codes, list)
            else [],
            "candidates": [],
        }
        candidates = raw_policy.get("candidates")
        if not isinstance(candidates, list):
            return frozen
        frozen_candidates: list[dict[str, object]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            credential_id = uuid_or_none(candidate.get("credential_id"))
            model = optional_string(candidate.get("model"))
            if credential_id is None or model is None:
                continue
            agent_settings = (
                {"model_api": candidate.get("model_api")}
                if "model_api" in candidate
                else {}
            )
            try:
                provider = self.model_provider_snapshot(
                    workspace_id,
                    None,
                    agent_snapshot={
                        "model": model,
                        "model_provider_credential_id": str(credential_id),
                        "model_settings": agent_settings,
                    },
                )
            except ValueError:
                continue
            frozen_candidates.append(
                {
                    "credential_id": provider.get("credential_id"),
                    "model": provider.get("selected_model"),
                    "provider": provider.get("provider"),
                    "model_api": provider.get("model_api"),
                }
            )
        frozen["candidates"] = frozen_candidates
        return frozen

    def model_provider_blocked_details(
        self,
        workspace_id: UUID,
        step: TaskStep,
    ) -> dict[str, object]:
        profile = (
            self.session.scalar(
                select(AgentProfile).where(
                    AgentProfile.workspace_id == workspace_id,
                    AgentProfile.id == step.assigned_agent_profile_id,
                )
            )
            if step.assigned_agent_profile_id is not None
            else None
        )
        credential: ModelProviderCredential | None = None
        if profile is not None and profile.model_provider_credential_id is not None:
            credential = self.session.scalar(
                select(ModelProviderCredential).where(
                    ModelProviderCredential.workspace_id == workspace_id,
                    ModelProviderCredential.id == profile.model_provider_credential_id,
                )
            )
        agent_provider = credential.provider if credential is not None else None
        source = "agent_model"
        if profile is not None and profile.model_provider_credential_id is not None:
            source = "agent_override"
        details: dict[str, object] = {
            "source": source,
            "workspace_id": str(workspace_id),
            "agent_profile_id": str(profile.id)
            if profile is not None and profile.id is not None
            else None,
            "agent_model": profile.model if profile is not None else None,
            "provider": agent_provider,
            "model_api": model_api_for_agent_provider(
                agent_provider,
                profile.model_settings if profile is not None else None,
                credential.budget_metadata if credential is not None else {},
            )
            if profile is not None
            else None,
            "requested_model_api": model_api(profile.model_settings)
            if profile is not None
            else None,
        }
        if credential is not None:
            credential_id = str(credential.id)
            details["credential"] = {
                "credential_id": credential_id,
                "provider": credential.provider,
                "status": credential.status,
                "health_status": credential.health_status,
                "failure_count": credential.failure_count,
                "last_failure_code": credential.last_failure_code,
                "budget_exhausted": budget_is_exhausted(credential.budget_metadata),
                "is_default": credential.is_default,
            }
            details.update(
                {
                    "credential_id": credential_id,
                    "credential_reference": f"model_provider_credentials:{credential_id}",
                    "credential_name": credential.name,
                    "default_model": credential.default_model,
                    "base_url_configured": bool(credential.base_url),
                    "is_default": credential.is_default,
                    "credential_status": credential.status,
                    "credential_health_status": credential.health_status,
                    "failure_count": credential.failure_count,
                    "budget_exhausted": budget_is_exhausted(credential.budget_metadata),
                    "last_failure_code": credential.last_failure_code,
                }
            )
        return redact_sensitive_payload(details)


def agent_runtime_profile_snapshot(
    *,
    workspace_id: UUID,
    profile: AgentProfile | None,
    agent_snapshot: dict[str, object] | None,
) -> dict[str, object]:
    if agent_snapshot is None:
        if profile is None:
            return {
                "id": None,
                "workspace_id": str(workspace_id),
                "version": 1,
                "name": "Default Agent",
                "role": "worker",
                "instructions": "Complete the assigned task.",
                "model": "gpt-4.1",
                "model_settings": {},
            }
        source: dict[str, object] = {
            "id": str(profile.id),
            "version": profile.version,
            "name": profile.name,
            "role": profile.role,
            "instructions": profile.instructions,
            "model": profile.model,
            "model_settings": dict(profile.model_settings or {}),
        }
    else:
        if profile is None:
            raise ValueError("Team agent snapshot references an unavailable agent profile")
        snapshot_profile_id = uuid_or_none(agent_snapshot.get("id"))
        if snapshot_profile_id != profile.id:
            raise ValueError("Team agent snapshot profile does not match the assigned agent")
        source = agent_snapshot
    profile_id = uuid_or_none(source.get("id"))
    version = source.get("version")
    name = source.get("name")
    role = source.get("role")
    instructions = source.get("instructions")
    model = source.get("model")
    model_settings = source.get("model_settings")
    if profile_id is None:
        raise ValueError("Agent runtime profile id is invalid")
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise ValueError("Agent runtime profile version is invalid")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Agent runtime profile name is invalid")
    if not isinstance(role, str) or not role.strip():
        raise ValueError("Agent runtime profile role is invalid")
    if not isinstance(instructions, str):
        raise ValueError("Agent runtime profile instructions are invalid")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Agent runtime profile model is invalid")
    if not isinstance(model_settings, dict):
        raise ValueError("Agent runtime profile model settings are invalid")
    return {
        "id": str(profile_id),
        "workspace_id": str(workspace_id),
        "version": version,
        "name": name,
        "role": role,
        "instructions": instructions,
        "model": model,
        "model_settings": dict(model_settings),
    }


def model_api(settings: dict[str, object] | None) -> str | None:
    if settings is None:
        return None
    return canonical_model_api(settings.get("model_api"))


def runtime_policy_snapshot(value: object) -> dict[str, object]:
    policy = dict_or_empty(value)
    raw_mcp = policy.get("mcp")
    mcp_policy = dict_or_empty(raw_mcp)
    network_mode = mcp_policy.get("network_mode")
    if not isinstance(network_mode, str) or not network_mode:
        network = policy.get("network")
        network_mode = network if isinstance(network, str) and network else "restricted"
    policy["mcp"] = {
        "network_mode": network_mode,
        "timeout_seconds": positive_int_or_default(mcp_policy.get("timeout_seconds"), 30),
        "max_input_bytes": positive_int_or_default(mcp_policy.get("max_input_bytes"), 64_000),
        "max_output_bytes": positive_int_or_default(
            mcp_policy.get("max_output_bytes"),
            256_000,
        ),
    }
    return policy
