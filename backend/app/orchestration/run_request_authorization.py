from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRuntimeToolContinuation
from backend.app.agents.models import AgentProfile
from backend.app.capabilities.models import (
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    WorkspaceSkillInstall,
)
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task, TaskStep

from .run_request_utils import dict_copy, expect_optional_uuid, string_list, uuid_or_none


@dataclass(slots=True)
class RunAuthorizationService:
    session: Session

    def allowed_tools_for_profile(self, profile: AgentProfile) -> tuple[str, ...]:
        tool_policy = profile.tool_policy if isinstance(profile.tool_policy, dict) else {}
        return allowed_tools_from_policy(tool_policy)

    def allowed_tools_for_snapshot(self, snapshot: dict[str, object]) -> tuple[str, ...]:
        tool_policy = snapshot.get("tool_policy")
        return allowed_tools_from_policy(tool_policy if isinstance(tool_policy, dict) else {})

    def allowed_tool_policy_for_run(
        self,
        snapshot: dict[str, object],
        profile: AgentProfile,
    ) -> tuple[str, ...]:
        snapshot_policy_tools = self.allowed_tools_for_snapshot(snapshot)
        if snapshot_policy_tools:
            return snapshot_policy_tools
        return self.allowed_tools_for_profile(profile)

    def allowed_tools_for_run(
        self,
        run: AgentRun,
        profile: AgentProfile,
    ) -> tuple[str, ...]:
        snapshot = self.authorization_snapshot_for_run(run)
        raw_tools = snapshot.get("allowed_tools")
        if isinstance(raw_tools, list):
            profile_tools = set(self.allowed_tool_policy_for_run(snapshot, profile))
            snapshot_tools = tuple(tool for tool in raw_tools if isinstance(tool, str))
            return tuple(tool for tool in snapshot_tools if tool in profile_tools)
        return self.allowed_tools_for_profile(profile)

    def authorization_snapshot_for_run(self, run: AgentRun) -> dict[str, object]:
        run_input = run.input if isinstance(run.input, dict) else {}
        snapshot = run_input.get("authorization_snapshot")
        return snapshot if isinstance(snapshot, dict) else {}

    def validate_authorization_snapshot(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile,
        snapshot: dict[str, object],
    ) -> None:
        if not snapshot:
            return
        expect_optional_uuid(snapshot, "workspace_id", run.workspace_id)
        expect_optional_uuid(snapshot, "task_id", run.task_id)
        expect_optional_uuid(snapshot, "task_step_id", run.task_step_id)
        expect_optional_uuid(snapshot, "agent_profile_id", run.agent_profile_id)
        expect_optional_uuid(snapshot, "runtime_space_id", run.runtime_space_id)
        if task is not None and task.workspace_id != run.workspace_id:
            raise ValueError("Authorization snapshot task workspace mismatch")
        raw_tools = snapshot.get("allowed_tools")
        if isinstance(raw_tools, list):
            profile_tools = set(self.allowed_tool_policy_for_run(snapshot, profile))
            snapshot_tools = {tool for tool in raw_tools if isinstance(tool, str)}
            extra_tools = snapshot_tools - profile_tools
            if extra_tools:
                raise ValueError("Authorization snapshot grants tools outside agent policy")
        installed_skills = snapshot.get("installed_skills")
        if isinstance(installed_skills, list):
            valid_install_snapshots = {
                str(item["install_id"]): item
                for item in self.installed_skill_snapshots(run.workspace_id, profile)
                if isinstance(item.get("install_id"), str)
            }
            for item in installed_skills:
                if not isinstance(item, dict):
                    continue
                install_id = item.get("install_id")
                if not isinstance(install_id, str):
                    continue
                valid_snapshot = valid_install_snapshots.get(install_id)
                if valid_snapshot is None:
                    raise ValueError(
                        "Authorization snapshot references unavailable workspace skill",
                    )
                if not skill_snapshot_matches(item, valid_snapshot):
                    raise ValueError(
                        "Authorization snapshot workspace skill provenance mismatch",
                    )

    def step_context_for_run(self, run: AgentRun) -> dict[str, object]:
        if run.task_step_id is None:
            return {}
        step = self.session.get(TaskStep, run.task_step_id)
        if step is None or step.workspace_id != run.workspace_id:
            raise ValueError("Run task step workspace mismatch")
        if run.task_id is not None and step.task_id != run.task_id:
            raise ValueError("Run task step does not belong to run task")
        if (
            run.agent_profile_id is not None
            and step.assigned_agent_profile_id is not None
            and step.assigned_agent_profile_id != run.agent_profile_id
        ):
            raise ValueError("Run agent profile is not assigned to task step")
        return {
            "context_scope": "task_step",
            "task_step_id": str(step.id),
            "work_package_id": step.work_package_id,
            "required_role": step.required_role,
            "required_skills": step.required_skills,
            "expected_artifacts": step.expected_artifacts,
            "acceptance_criteria": step.acceptance_criteria,
            "review_policy": step.review_policy,
        }

    def installed_skill_snapshots(
        self,
        workspace_id: UUID,
        profile: AgentProfile | None,
    ) -> list[dict[str, object]]:
        if profile is None or not isinstance(profile.skills, dict):
            return []
        install_ids = string_list(profile.skills.get("installed_skill_ids"))
        if not install_ids:
            return []
        valid_install_ids = [
            install_uuid for install_id in install_ids if (install_uuid := uuid_or_none(install_id))
        ]
        if not valid_install_ids:
            return []
        installs = self.session.scalars(
            select(WorkspaceSkillInstall).where(
                WorkspaceSkillInstall.workspace_id == workspace_id,
                WorkspaceSkillInstall.status == "active",
                WorkspaceSkillInstall.id.in_(valid_install_ids),
            )
        ).all()
        by_id = {str(install.id): install for install in installs}
        mcp_tool_snapshots = self.installed_skill_mcp_tool_snapshots(workspace_id)
        snapshots: list[dict[str, object]] = []
        for install_id in install_ids:
            install = by_id.get(install_id)
            if install is None:
                continue
            installed_capability_keys = list(install.installed_capability_keys)
            matched_mcp_tools = [
                tool
                for tool in mcp_tool_snapshots
                if skill_mcp_tool_matches(tool, installed_capability_keys)
            ]
            snapshots.append(
                {
                    "install_id": str(install.id),
                    "source_skill_id": str(install.skill_id),
                    "installed_key": install.installed_key,
                    "installed_name": install.installed_name,
                    "installed_version": install.installed_version,
                    "installed_capability_keys": installed_capability_keys,
                    "source_checksum": install.source_checksum,
                    "source_visibility": install.source_visibility,
                    "mcp_tools": matched_mcp_tools,
                    "mcp_credential_references": credential_refs_for_tool_snapshots(
                        matched_mcp_tools,
                    ),
                }
            )
        return snapshots

    def installed_skill_mcp_tool_snapshots(self, workspace_id: UUID) -> list[dict[str, object]]:
        rows = self.session.execute(
            select(McpToolAllowlist, McpServer)
            .join(
                McpServer,
                McpServer.id == McpToolAllowlist.mcp_server_id,
            )
            .where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.status == "active",
                McpServer.status == "active",
            )
            .order_by(McpServer.name.asc(), McpToolAllowlist.tool_name.asc())
        ).all()
        credential_refs = self.mcp_credential_reference_snapshots(workspace_id)
        tools: list[dict[str, object]] = []
        for allow, server in rows:
            refs = [
                ref for ref in credential_refs if ref.get("mcp_server_id") in {str(server.id), None}
            ]
            tools.append(
                {
                    "allowlist_id": str(allow.id),
                    "mcp_server_id": str(server.id),
                    "mcp_server_name": server.name,
                    "server_type": server.server_type,
                    "tool_name": allow.tool_name,
                    "capability_key": allow.capability_key,
                    "requires_approval": allow.requires_approval,
                    "risk_level": allow.risk_level,
                    "policy": dict_copy(allow.policy),
                    "credential_reference_ids": [
                        str(ref["credential_reference_id"]) for ref in refs
                    ],
                    "credential_references": refs,
                }
            )
        return tools

    def mcp_credential_reference_snapshots(
        self,
        workspace_id: UUID,
    ) -> list[dict[str, object]]:
        refs = self.session.scalars(
            select(McpCredentialReference)
            .where(
                McpCredentialReference.workspace_id == workspace_id,
                McpCredentialReference.status == "active",
            )
            .order_by(McpCredentialReference.created_at.asc())
        ).all()
        return [
            {
                "credential_reference_id": str(ref.id),
                "mcp_server_id": str(ref.mcp_server_id) if ref.mcp_server_id is not None else None,
                "name": ref.name,
                "provider": ref.provider,
                "secret_fingerprint": ref.secret_fingerprint,
                "encryption_key_id": ref.encryption_key_id,
                "scopes": list(ref.scopes),
            }
            for ref in refs
        ]


def allowed_tools_from_policy(tool_policy: dict[str, object]) -> tuple[str, ...]:
    raw_tools = tool_policy.get("allowed_tools")
    if raw_tools is None:
        raw_tools = tool_policy.get("mcp_tools")
    if not isinstance(raw_tools, list):
        return ()
    return tuple(tool for tool in raw_tools if isinstance(tool, str))


def tool_continuations_for_run(
    run_input: dict[str, object],
) -> tuple[AgentRuntimeToolContinuation, ...]:
    results = run_input.get("pending_tool_results")
    if not isinstance(results, list):
        return ()
    continuations: list[AgentRuntimeToolContinuation] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        tool_name = item.get("tool_name")
        status = item.get("status")
        if not isinstance(tool_name, str) or not isinstance(status, str):
            continue
        metadata = {
            key: value
            for key, value in item.items()
            if key not in {"tool_name", "status", "response", "error", "request"}
        }
        continuations.append(
            AgentRuntimeToolContinuation(
                tool_name=tool_name,
                status=status,
                result=dict_copy(item.get("response")),
                error=dict_copy(item.get("error")),
                metadata=dict_copy(metadata),
            )
        )
    return tuple(continuations)


def skill_snapshot_matches(
    snapshot_item: dict[str, object],
    current_item: dict[str, object],
) -> bool:
    comparable_keys = (
        "install_id",
        "source_skill_id",
        "installed_key",
        "installed_name",
        "installed_version",
        "source_checksum",
        "source_visibility",
    )
    for key in comparable_keys:
        if snapshot_item.get(key) != current_item.get(key):
            return False
    snapshot_caps = snapshot_item.get("installed_capability_keys")
    current_caps = current_item.get("installed_capability_keys")
    if (isinstance(snapshot_caps, list) or isinstance(current_caps, list)) and (
        snapshot_caps != current_caps
    ):
        return False
    if not optional_list_matches(
        snapshot_item.get("mcp_tools"),
        current_item.get("mcp_tools"),
    ):
        return False
    return optional_list_matches(
        snapshot_item.get("mcp_credential_references"),
        current_item.get("mcp_credential_references"),
    )


def skill_mcp_tool_matches(
    tool_snapshot: dict[str, object],
    installed_capability_keys: list[str],
) -> bool:
    capability_key = tool_snapshot.get("capability_key")
    if isinstance(capability_key, str) and capability_key:
        return capability_key in installed_capability_keys
    policy = tool_snapshot.get("policy")
    if isinstance(policy, dict):
        policy_caps = string_list(policy.get("capability_keys"))
        if policy_caps:
            return any(capability in installed_capability_keys for capability in policy_caps)
    return False


def credential_refs_for_tool_snapshots(
    tool_snapshots: list[dict[str, object]],
) -> list[dict[str, object]]:
    refs_by_id: dict[str, dict[str, object]] = {}
    for tool in tool_snapshots:
        credential_refs = tool.get("credential_references")
        if not isinstance(credential_refs, list):
            continue
        for credential_ref in credential_refs:
            if not isinstance(credential_ref, dict):
                continue
            credential_ref_id = credential_ref.get("credential_reference_id")
            if isinstance(credential_ref_id, str):
                refs_by_id.setdefault(credential_ref_id, credential_ref)
    return list(refs_by_id.values())


def optional_list_matches(snapshot_value: object, current_value: object) -> bool:
    if isinstance(snapshot_value, list):
        return snapshot_value == current_value
    return not isinstance(current_value, list) or current_value == []
