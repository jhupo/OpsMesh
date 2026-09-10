from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.guardrails import runtime_controls_snapshot
from backend.app.agents.models import AgentProfile
from backend.app.capabilities.effective_catalog import (
    EffectiveCapabilityCatalogService,
    effective_catalog_fingerprint,
)
from backend.app.model_providers.metadata import budget_is_exhausted
from backend.app.model_providers.model_api import (
    canonical_model_api,
    model_api_for_agent_provider,
)
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.model_providers.resolution import ModelProviderResolutionService
from backend.app.orchestration.run_agent_tool_authorization import (
    AgentToolAuthorizationSnapshotService,
)
from backend.app.orchestration.run_authorization_integrity import (
    authorization_snapshot_fingerprint,
)
from backend.app.orchestration.run_request_builder import RunRequestBuilder
from backend.app.orchestration.run_request_utils import dict_copy, uuid_or_none
from backend.app.orchestration.run_runtime_authorization import (
    RunRuntimeAuthorizationService,
)
from backend.app.planning.agent_plan import is_agent_planning_step, planner_output_schema
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.tasks.models import Task, TaskStep


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
        effective_catalog = (
            EffectiveCapabilityCatalogService(self.session).build(
                workspace_id=task.workspace_id,
                agent_profile_id=profile.id,
                team_id=task.agent_team_id,
            )
            if profile is not None
            else None
        )
        if is_agent_planning_step(step) and effective_catalog is not None:
            effective_catalog.tools = []
            effective_catalog.fingerprint = effective_catalog_fingerprint(effective_catalog)
        catalog_snapshot = (
            effective_catalog.model_dump(mode="json") if effective_catalog is not None else None
        )
        allowed_tools = (
            [item.descriptor.name for item in effective_catalog.tools]
            if effective_catalog is not None
            else []
        )
        tool_policy = profile.tool_policy if profile is not None else {}
        runtime_policy = profile.runtime_policy if profile is not None else {}
        memory_policy = profile.memory_policy if profile is not None else {}
        approval_policy = profile.approval_policy if profile is not None else {}
        model_provider = self.model_provider_snapshot(
            task.workspace_id,
            profile,
            agent_snapshot=agent_snapshot,
        )
        frozen_runtime_policy = runtime_policy_snapshot(runtime_policy)
        runtime_controls = runtime_controls_snapshot(
            model_settings=profile.model_settings if profile is not None else {},
            runtime_policy=frozen_runtime_policy,
        )
        if is_agent_planning_step(step):
            runtime_controls["output_schema"] = asdict(planner_output_schema())
        runtime_binding = RunRuntimeAuthorizationService(self.session).resolve_for_snapshot(
            task=task,
            step=step,
            capability_catalog=catalog_snapshot,
            runtime_policy=frozen_runtime_policy,
        )
        agent_tools = (
            []
            if is_agent_planning_step(step)
            else AgentToolAuthorizationSnapshotService(self.session).build(
                task=task,
                source_profile=profile,
                source_catalog=catalog_snapshot,
                source_model_provider=model_provider,
                file_scope_ids=runtime_binding.allowed_file_ids,
                model_provider_snapshot=lambda target: self.model_provider_snapshot(
                    task.workspace_id,
                    target,
                ),
            )
        )
        snapshot: dict[str, object] = {
            "version": 2,
            "workspace_id": str(task.workspace_id),
            "task_id": str(task.id),
            "task_step_id": str(step.id) if step is not None else None,
            "runtime_space_id": str(runtime_binding.runtime_space_id)
            if runtime_binding.runtime_space_id is not None
            else None,
            "agent_profile_id": str(profile.id)
            if profile is not None and profile.id is not None
            else None,
            "allowed_tools": allowed_tools,
            "capability_catalog": catalog_snapshot,
            "tool_policy": dict_copy(tool_policy),
            "installed_skills": self.request_builder.installed_skill_snapshots(
                task.workspace_id,
                profile,
            ),
            "model_provider": model_provider,
            "runtime_policy": frozen_runtime_policy,
            "memory_policy": dict_copy(memory_policy),
            "approval_policy": dict_copy(approval_policy),
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
        snapshot["fingerprint"] = authorization_snapshot_fingerprint(snapshot)
        return snapshot

    def model_provider_snapshot(
        self,
        workspace_id: UUID,
        profile: AgentProfile | None,
        *,
        agent_snapshot: dict[str, object] | None = None,
    ) -> dict[str, object]:
        agent_model = (
            optional_string(agent_snapshot.get("model")) if agent_snapshot is not None else None
        ) or (profile.model if profile is not None else "gpt-4.1")
        credential_id = (
            uuid_or_none(agent_snapshot.get("model_provider_credential_id"))
            if agent_snapshot is not None
            else None
        )
        if credential_id is None and agent_snapshot is None and profile is not None:
            credential_id = profile.model_provider_credential_id
        agent_model_settings = (
            {"model_api": agent_snapshot.get("model_api")}
            if agent_snapshot is not None and "model_api" in agent_snapshot
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


def model_api(settings: dict[str, object] | None) -> str | None:
    if settings is None:
        return None
    return canonical_model_api(settings.get("model_api"))


def runtime_policy_snapshot(value: object) -> dict[str, object]:
    policy = dict_copy(value)
    raw_mcp = policy.get("mcp")
    mcp_policy = dict_copy(raw_mcp)
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


def positive_int_or_default(value: object, default: int) -> int:
    return value if isinstance(value, int) and value > 0 else default


def optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None
