from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.core.contracts import (
    AgentRuntimeResourceGrant,
    AgentRuntimeToolContinuation,
    AgentRuntimeToolDefinition,
)
from backend.app.agents.models import AgentProfile
from backend.app.capabilities.effective_catalog import effective_catalog_fingerprint
from backend.app.capabilities.models import (
    CapabilityResource,
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    WorkspaceSkillInstall,
)
from backend.app.orchestration.run_authorization_integrity import (
    authorization_snapshot_fingerprint,
)
from backend.app.orchestration.run_events import RunEventRecorder
from backend.app.orchestration.run_runtime_authorization import (
    RunRuntimeAuthorizationError,
    RunRuntimeAuthorizationService,
)
from backend.app.runs.models import AgentRun
from backend.app.security.models import SecurityEvent
from backend.app.tasks.models import Task, TaskStep
from backend.app.tasks.ownership import task_owner_can_execute_step

from .run_request_utils import dict_copy, expect_optional_uuid, string_list, uuid_or_none


@dataclass(slots=True)
class RunAuthorizationService:
    session: Session

    def allowed_tools_for_run(
        self,
        run: AgentRun,
        profile: AgentProfile,
    ) -> tuple[str, ...]:
        snapshot = self.authorization_snapshot_for_run(run)
        _ = profile
        raw_tools = snapshot.get("allowed_tools")
        if not isinstance(raw_tools, list) or not all(isinstance(tool, str) for tool in raw_tools):
            raise ValueError("Authorization snapshot allowed tools are invalid")
        return tuple(raw_tools)

    def authorization_snapshot_for_run(self, run: AgentRun) -> dict[str, object]:
        run_input = run.input if isinstance(run.input, dict) else {}
        snapshot = run_input.get("authorization_snapshot")
        return snapshot if isinstance(snapshot, dict) else {}

    def validate_authorization_snapshot(
        self,
        run: AgentRun,
        task: Task | None,
        profile: AgentProfile | None,
        snapshot: dict[str, object],
        *,
        lock_resources: bool = False,
    ) -> None:
        _ = profile
        if not snapshot:
            raise ValueError("Authorization snapshot is required")
        if snapshot.get("version") != 2:
            raise ValueError("Authorization snapshot version is unsupported")
        fingerprint = snapshot.get("fingerprint")
        if not isinstance(fingerprint, str) or fingerprint != authorization_snapshot_fingerprint(
            snapshot
        ):
            raise ValueError("Authorization snapshot fingerprint mismatch")
        expect_optional_uuid(snapshot, "workspace_id", run.workspace_id)
        expect_optional_uuid(snapshot, "task_id", run.task_id)
        expect_optional_uuid(snapshot, "task_step_id", run.task_step_id)
        expect_optional_uuid(snapshot, "agent_profile_id", run.agent_profile_id)
        expect_optional_uuid(snapshot, "runtime_space_id", run.runtime_space_id)
        if task is not None and task.workspace_id != run.workspace_id:
            raise ValueError("Authorization snapshot task workspace mismatch")
        if task is not None and "task_owner_agent_profile_id" in snapshot:
            expected_owner = (
                str(task.owner_agent_profile_id)
                if task.owner_agent_profile_id is not None
                else None
            )
            if snapshot.get("task_owner_agent_profile_id") != expected_owner:
                raise ValueError("Authorization snapshot task owner changed")
            expected_version = max(int(task.owner_version or 1), 1)
            if snapshot.get("task_owner_version") != expected_version:
                raise ValueError("Authorization snapshot task owner version changed")
        try:
            RunRuntimeAuthorizationService(self.session).validate_for_run(
                run=run,
                task=task,
                snapshot=snapshot,
            )
        except RunRuntimeAuthorizationError as exc:
            self._record_runtime_denial(run, exc)
            raise
        catalog = capability_catalog_for_snapshot(snapshot)
        if catalog is None:
            if snapshot.get("allowed_tools") not in ([], None):
                raise ValueError("Authorization snapshot capability catalog is missing")
            return
        catalog_fingerprint = catalog.get("fingerprint")
        if not isinstance(
            catalog_fingerprint, str
        ) or catalog_fingerprint != effective_catalog_fingerprint(catalog):
            raise ValueError("Capability catalog fingerprint mismatch")
        expect_optional_uuid(catalog, "workspace_id", run.workspace_id)
        expect_optional_uuid(catalog, "agent_profile_id", run.agent_profile_id)
        if task is not None:
            expect_optional_uuid(catalog, "team_id", task.agent_team_id)
        definitions = tool_definitions_for_snapshot(snapshot)
        allowed_tools = snapshot.get("allowed_tools")
        if not isinstance(allowed_tools, list) or allowed_tools != [
            definition.name for definition in definitions
        ]:
            raise ValueError("Authorization snapshot tool manifest mismatch")
        self._require_active_capability_resources(
            run.workspace_id,
            resource_grants_for_snapshot(snapshot),
            lock=lock_resources,
        )

    def _record_runtime_denial(
        self,
        run: AgentRun,
        denial: RunRuntimeAuthorizationError,
    ) -> None:
        RunEventRecorder(self.session).append_event(
            run,
            "runtime.authorization_blocked",
            str(denial),
            {"reason": denial.code},
        )
        self.session.add(
            SecurityEvent(
                workspace_id=run.workspace_id,
                user_id=None,
                action="agent_runtime.authorization_blocked",
                outcome="blocked",
                severity="high",
                source_ip=None,
                user_agent=None,
                request_id=None,
                path="internal:agent_runtime_gateway",
                method="WORKER",
                reason=denial.code,
                event_metadata={
                    "agent_run_id": str(run.id),
                    "workspace_runtime_id": str(run.runtime_id)
                    if run.runtime_id is not None
                    else None,
                    "runtime_space_id": str(run.runtime_space_id)
                    if run.runtime_space_id is not None
                    else None,
                    "authorization_snapshot_fingerprint": self.authorization_snapshot_for_run(
                        run
                    ).get("fingerprint"),
                },
                created_at=datetime.now(UTC),
            )
        )
        self.session.flush()

    def _require_active_capability_resources(
        self,
        workspace_id: UUID,
        grants: tuple[AgentRuntimeResourceGrant, ...],
        *,
        lock: bool,
    ) -> None:
        if not grants:
            return
        statement = select(CapabilityResource).where(
            CapabilityResource.workspace_id == workspace_id,
            CapabilityResource.status == "active",
            CapabilityResource.id.in_([grant.resource_id for grant in grants]),
        )
        if lock:
            statement = statement.with_for_update()
        resources = {
            resource.id: resource
            for resource in self.session.scalars(statement).all()
        }
        if len(resources) != len(grants):
            raise ValueError("Authorization snapshot references a disabled capability resource")
        for grant in grants:
            resource = resources.get(grant.resource_id)
            if resource is None:
                raise ValueError(
                    "Authorization snapshot references a disabled capability resource"
                )
            if (
                resource.version != grant.version
                or resource.resource_type != grant.resource_type
                or resource.access_mode != grant.access_mode
                or resource.locator != grant.locator
            ):
                raise ValueError("Authorization snapshot capability resource has changed")

    def step_context_for_run(self, run: AgentRun) -> dict[str, object]:
        if run.task_step_id is None:
            return {}
        step = self.session.get(TaskStep, run.task_step_id)
        if step is None or step.workspace_id != run.workspace_id:
            raise ValueError("Run task step workspace mismatch")
        if run.task_id is not None and step.task_id != run.task_id:
            raise ValueError("Run task step does not belong to run task")
        if run.task_id is not None:
            task = self.session.get(Task, run.task_id)
            if task is None or task.workspace_id != run.workspace_id:
                raise ValueError("Run task workspace mismatch")
            if not task_owner_can_execute_step(task, step):
                raise ValueError("Task owner changed; run must be recreated")
        if (
            run.agent_profile_id is not None
            and step.assigned_agent_profile_id is not None
            and step.assigned_agent_profile_id != run.agent_profile_id
        ):
            raise ValueError("Run agent profile is not assigned to task step")
        task_owner_agent_profile_id = (
            str(task.owner_agent_profile_id)
            if task is not None and task.owner_agent_profile_id is not None
            else None
        )
        task_owner_version = max(int(task.owner_version or 1), 1) if task is not None else None
        return {
            "context_scope": "task_step",
            "task_step_id": str(step.id),
            "task_owner_agent_profile_id": task_owner_agent_profile_id,
            "task_owner_version": task_owner_version,
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


def capability_catalog_for_snapshot(
    snapshot: dict[str, object],
) -> dict[str, object] | None:
    catalog = snapshot.get("capability_catalog")
    return catalog if isinstance(catalog, dict) else None


def tool_definitions_for_snapshot(
    snapshot: dict[str, object],
) -> tuple[AgentRuntimeToolDefinition, ...]:
    catalog = capability_catalog_for_snapshot(snapshot)
    raw_tools = catalog.get("tools") if catalog is not None else None
    if raw_tools is None:
        return ()
    if not isinstance(raw_tools, list):
        raise ValueError("Capability catalog tools are invalid")
    definitions: list[AgentRuntimeToolDefinition] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            raise ValueError("Capability catalog tool entry is invalid")
        descriptor = item.get("descriptor")
        if not isinstance(descriptor, dict):
            raise ValueError("Capability catalog tool descriptor is invalid")
        name = descriptor.get("name")
        source = descriptor.get("source")
        description = descriptor.get("description")
        input_schema = descriptor.get("input_schema")
        parameters = item.get("parameters")
        locked_parameters = item.get("locked_parameters")
        if (
            not isinstance(name, str)
            or not isinstance(source, str)
            or not isinstance(description, str)
            or not isinstance(input_schema, dict)
            or not isinstance(parameters, dict)
            or not isinstance(locked_parameters, list)
            or not all(isinstance(field, str) for field in locked_parameters)
        ):
            raise ValueError("Capability catalog tool contract is invalid")
        definitions.append(
            AgentRuntimeToolDefinition(
                name=name,
                source=source,
                description=description,
                input_schema=dict(input_schema),
                parameters=dict(parameters),
                locked_parameters=tuple(locked_parameters),
                requires_approval=descriptor.get("requires_approval") is True,
                risk_level=str(descriptor.get("risk_level") or "low"),
                required_resource_type=(
                    str(descriptor["required_resource_type"])
                    if descriptor.get("required_resource_type") is not None
                    else None
                ),
                required_access_modes=tuple(
                    item
                    for item in descriptor.get("required_access_modes", [])
                    if isinstance(item, str)
                ),
                mcp_server_id=uuid_or_none(descriptor.get("mcp_server_id")),
                mcp_tool_allowlist_id=uuid_or_none(descriptor.get("mcp_tool_allowlist_id")),
            )
        )
    if len({definition.name for definition in definitions}) != len(definitions):
        raise ValueError("Capability catalog contains duplicate tool names")
    return tuple(definitions)


def resource_grants_for_snapshot(
    snapshot: dict[str, object],
) -> tuple[AgentRuntimeResourceGrant, ...]:
    catalog = capability_catalog_for_snapshot(snapshot)
    raw_resources = catalog.get("resources") if catalog is not None else None
    if raw_resources is None:
        return ()
    if not isinstance(raw_resources, list):
        raise ValueError("Capability catalog resources are invalid")
    grants: list[AgentRuntimeResourceGrant] = []
    for item in raw_resources:
        if not isinstance(item, dict):
            raise ValueError("Capability catalog resource entry is invalid")
        resource = item.get("resource")
        parameters = item.get("parameters")
        if not isinstance(resource, dict) or not isinstance(parameters, dict):
            raise ValueError("Capability catalog resource contract is invalid")
        resource_id = uuid_or_none(resource.get("id"))
        resource_type = resource.get("resource_type")
        access_mode = resource.get("access_mode")
        locator = resource.get("locator")
        version = resource.get("version")
        if (
            resource_id is None
            or not isinstance(resource_type, str)
            or not isinstance(access_mode, str)
            or not isinstance(locator, dict)
            or not isinstance(version, int)
        ):
            raise ValueError("Capability catalog resource fields are invalid")
        grants.append(
            AgentRuntimeResourceGrant(
                resource_id=resource_id,
                resource_type=resource_type,
                access_mode=access_mode,
                locator=dict(locator),
                parameters=dict(parameters),
                version=version,
            )
        )
    if len({grant.resource_id for grant in grants}) != len(grants):
        raise ValueError("Capability catalog contains duplicate resource grants")
    return tuple(grants)


def file_scope_ids_for_snapshot(snapshot: dict[str, object]) -> tuple[UUID, ...]:
    file_scope = snapshot.get("file_scope")
    raw_ids = file_scope.get("allowed_file_ids") if isinstance(file_scope, dict) else None
    if not isinstance(raw_ids, list):
        return ()
    ids: list[UUID] = []
    for item in raw_ids:
        file_id = uuid_or_none(item)
        if file_id is None:
            raise ValueError("Authorization snapshot file scope is invalid")
        ids.append(file_id)
    return tuple(ids)


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
