from datetime import datetime

from backend.app.core.utils import stringify_or_none
from backend.app.domains.agents.memory.models import (
    WorkspaceMemoryConfiguration,
    WorkspaceMemoryEntry,
    WorkspaceMemoryVersion,
)
from backend.app.domains.agents.profiles.models import AgentProfile
from backend.app.domains.capabilities.skills.models import WorkspaceSkillInstall
from backend.app.domains.orchestration.runs.models import AgentRun, RunEvent
from backend.app.domains.orchestration.tasks.models import Task, TaskMessage, TaskStep
from backend.app.domains.workspace.projects.models import (
    WorkspaceProject,
    WorkspaceProjectConfigurationVersion,
    WorkspaceProjectFile,
    WorkspaceProjectOutput,
)
from backend.app.domains.workspace.storage.artifact_models import Artifact
from backend.app.domains.workspace.storage.models import WorkspaceFile
from backend.app.domains.workspace.teams.models import AgentTeam, AgentTeamMember
from backend.app.domains.workspace.tenants.models import Workspace
from backend.app.observability.audit.models import AuditEvent
from backend.app.runtime.environment.spaces.models import RuntimeSpace, RuntimeSpaceQuota


def _workspace_payload(workspace: Workspace) -> dict[str, object]:
    return {
        "id": str(workspace.id),
        "owner_user_id": str(workspace.owner_user_id),
        "name": workspace.name,
        "slug": workspace.slug,
        "settings": workspace.settings,
        "status": workspace.status,
        "created_at": _dt(workspace.created_at),
        "updated_at": _dt(workspace.updated_at),
    }


def _agent_payload(agent: AgentProfile) -> dict[str, object]:
    return {
        "id": str(agent.id),
        "workspace_id": str(agent.workspace_id),
        "name": agent.name,
        "role": agent.role,
        "description": agent.description,
        "instructions": agent.instructions,
        "model": agent.model,
        "model_settings": agent.model_settings,
        "capabilities": agent.capabilities,
        "skills": agent.skills,
        "tool_policy": agent.tool_policy,
        "runtime_policy": agent.runtime_policy,
        "memory_policy": agent.memory_policy,
        "approval_policy": agent.approval_policy,
        "version": agent.version,
        "status": agent.status,
        "created_at": _dt(agent.created_at),
        "updated_at": _dt(agent.updated_at),
    }


def _team_payload(team: AgentTeam) -> dict[str, object]:
    return {
        "id": str(team.id),
        "workspace_id": str(team.workspace_id),
        "name": team.name,
        "team_type": team.team_type,
        "description": team.description,
        "manager_agent_profile_id": stringify_or_none(team.manager_agent_profile_id),
        "runtime_space_id": stringify_or_none(team.runtime_space_id),
        "coordination_rules": team.coordination_rules,
        "default_task_policy": team.default_task_policy,
        "capability_policy": team.capability_policy,
        "capability_policy_version": team.capability_policy_version,
        "status": team.status,
        "created_at": _dt(team.created_at),
        "updated_at": _dt(team.updated_at),
    }


def _team_member_payload(member: AgentTeamMember) -> dict[str, object]:
    return {
        "id": str(member.id),
        "workspace_id": str(member.workspace_id),
        "agent_team_id": str(member.agent_team_id),
        "agent_profile_id": str(member.agent_profile_id),
        "reports_to_member_id": stringify_or_none(member.reports_to_member_id),
        "team_role": member.team_role,
        "department": member.department,
        "position_title": member.position_title,
        "responsibilities": member.responsibilities,
        "skill_weights": member.skill_weights,
        "availability": member.availability,
        "max_concurrent_tasks": member.max_concurrent_tasks,
        "accepts_tasks": member.accepts_tasks,
        "is_required": member.is_required,
        "order_index": member.order_index,
        "status": member.status,
    }


def _task_payload(task: Task) -> dict[str, object]:
    return {
        "id": str(task.id),
        "workspace_id": str(task.workspace_id),
        "created_by_user_id": stringify_or_none(task.created_by_user_id),
        "created_by_agent_run_id": stringify_or_none(task.created_by_agent_run_id),
        "agent_team_id": stringify_or_none(task.agent_team_id),
        "runtime_space_id": stringify_or_none(task.runtime_space_id),
        "domain_type": task.domain_type,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "input": task.input,
        "generic_state": task.generic_state,
        "domain_state": task.domain_state,
        "team_snapshot": task.team_snapshot,
        "project_plan": task.project_plan,
        "final_output": task.final_output,
        "completed_at": _dt_or_none(task.completed_at),
        "created_at": _dt(task.created_at),
        "updated_at": _dt(task.updated_at),
    }


def _task_step_payload(step: TaskStep) -> dict[str, object]:
    return {
        "id": str(step.id),
        "workspace_id": str(step.workspace_id),
        "task_id": str(step.task_id),
        "assigned_agent_profile_id": stringify_or_none(step.assigned_agent_profile_id),
        "runtime_space_id": stringify_or_none(step.runtime_space_id),
        "work_package_id": step.work_package_id,
        "required_role": step.required_role,
        "required_skills": step.required_skills,
        "expected_artifacts": step.expected_artifacts,
        "acceptance_criteria": step.acceptance_criteria,
        "review_policy": step.review_policy,
        "title": step.title,
        "description": step.description,
        "status": step.status,
        "order_index": step.order_index,
        "dependencies": step.dependencies,
        "result_summary": step.result_summary,
        "created_at": _dt(step.created_at),
        "updated_at": _dt(step.updated_at),
    }


def _task_message_payload(message: TaskMessage) -> dict[str, object]:
    return {
        "id": str(message.id),
        "workspace_id": str(message.workspace_id),
        "task_id": str(message.task_id),
        "task_step_id": stringify_or_none(message.task_step_id),
        "agent_run_id": stringify_or_none(message.agent_run_id),
        "agent_profile_id": stringify_or_none(message.agent_profile_id),
        "message_type": message.message_type,
        "sequence": message.sequence,
        "body": message.body,
        "payload": message.payload,
        "created_at": _dt(message.created_at),
        "updated_at": _dt(message.updated_at),
    }


def _run_payload(run: AgentRun) -> dict[str, object]:
    return {
        "id": str(run.id),
        "workspace_id": str(run.workspace_id),
        "task_id": stringify_or_none(run.task_id),
        "task_step_id": stringify_or_none(run.task_step_id),
        "agent_profile_id": stringify_or_none(run.agent_profile_id),
        "runtime_id": stringify_or_none(run.runtime_id),
        "runtime_space_id": stringify_or_none(run.runtime_space_id),
        "status": run.status,
        "input": run.input,
        "output": run.output,
        "error": run.error,
        "model": run.model,
        "started_at": _dt_or_none(run.started_at),
        "completed_at": _dt_or_none(run.completed_at),
        "created_at": _dt(run.created_at),
        "updated_at": _dt(run.updated_at),
    }


def _run_event_payload(event: RunEvent) -> dict[str, object]:
    return {
        "id": str(event.id),
        "workspace_id": str(event.workspace_id),
        "agent_run_id": str(event.agent_run_id),
        "event_type": event.event_type,
        "sequence": event.sequence,
        "message": event.message,
        "metadata": event.event_metadata,
        "created_at": _dt(event.created_at),
    }


def _file_payload(file: WorkspaceFile) -> dict[str, object]:
    return {
        "id": str(file.id),
        "workspace_id": str(file.workspace_id),
        "uploaded_by_user_id": stringify_or_none(file.uploaded_by_user_id),
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": file.size_bytes,
        "checksum_sha256": file.checksum_sha256,
        "status": file.status,
        "sensitivity": file.sensitivity,
        "runtime_access": file.runtime_access,
        "metadata": file.file_metadata,
        "created_at": _dt(file.created_at),
        "updated_at": _dt(file.updated_at),
    }


def _artifact_payload(artifact: Artifact) -> dict[str, object]:
    return {
        "id": str(artifact.id),
        "workspace_id": str(artifact.workspace_id),
        "task_id": stringify_or_none(artifact.task_id),
        "agent_run_id": stringify_or_none(artifact.agent_run_id),
        "task_step_id": stringify_or_none(artifact.task_step_id),
        "agent_profile_id": stringify_or_none(artifact.agent_profile_id),
        "supersedes_artifact_id": stringify_or_none(artifact.supersedes_artifact_id),
        "work_package_id": artifact.work_package_id,
        "version": artifact.version,
        "review_status": artifact.review_status,
        "artifact_type": artifact.artifact_type,
        "filename": artifact.filename,
        "content_type": artifact.content_type,
        "size_bytes": artifact.size_bytes,
        "checksum_sha256": artifact.checksum_sha256,
        "metadata": artifact.artifact_metadata,
        "created_at": _dt(artifact.created_at),
    }


def _runtime_space_payload(runtime_space: RuntimeSpace) -> dict[str, object]:
    return {
        "id": str(runtime_space.id),
        "workspace_id": str(runtime_space.workspace_id),
        "created_by_user_id": stringify_or_none(runtime_space.created_by_user_id),
        "default_runtime_template_id": stringify_or_none(runtime_space.default_runtime_template_id),
        "name": runtime_space.name,
        "scope": runtime_space.scope,
        "status": runtime_space.status,
        "policy": runtime_space.policy,
        "network_policy": runtime_space.network_policy,
        "storage_policy": runtime_space.storage_policy,
        "cleanup_policy": runtime_space.cleanup_policy,
        "created_at": _dt(runtime_space.created_at),
        "updated_at": _dt(runtime_space.updated_at),
    }


def _runtime_space_quota_payload(quota: RuntimeSpaceQuota) -> dict[str, object]:
    return {
        "id": str(quota.id),
        "workspace_id": str(quota.workspace_id),
        "runtime_space_id": str(quota.runtime_space_id),
        "quota_key": quota.quota_key,
        "limit_value": quota.limit_value,
        "reserved_value": quota.reserved_value,
        "unit": quota.unit,
        "status": quota.status,
        "created_at": _dt(quota.created_at),
        "updated_at": _dt(quota.updated_at),
    }


def _skill_install_payload(install: WorkspaceSkillInstall) -> dict[str, object]:
    return {
        "id": str(install.id),
        "workspace_id": str(install.workspace_id),
        "skill_id": str(install.skill_id),
        "installed_by_user_id": stringify_or_none(install.installed_by_user_id),
        "installed_key": install.installed_key,
        "installed_name": install.installed_name,
        "installed_version": install.installed_version,
        "installed_description": install.installed_description,
        "installed_capability_keys": install.installed_capability_keys,
        "installed_manifest": install.installed_manifest,
        "source_owner_workspace_id": stringify_or_none(install.source_owner_workspace_id),
        "source_visibility": install.source_visibility,
        "source_checksum": install.source_checksum,
        "config": install.config,
        "status": install.status,
        "created_at": _dt(install.created_at),
        "updated_at": _dt(install.updated_at),
    }


def _audit_payload(event: AuditEvent) -> dict[str, object]:
    return {
        "id": str(event.id),
        "workspace_id": str(event.workspace_id),
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "user_id": stringify_or_none(event.user_id),
        "agent_run_id": stringify_or_none(event.agent_run_id),
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "metadata": event.audit_metadata,
        "created_at": _dt(event.created_at),
    }


def _project_payload(project: WorkspaceProject) -> dict[str, object]:
    return {
        "id": str(project.id),
        "workspace_id": str(project.workspace_id),
        "created_by_user_id": stringify_or_none(project.created_by_user_id),
        "name": project.name,
        "slug": project.slug,
        "description": project.description,
        "input_path": project.input_path,
        "work_path": project.work_path,
        "output_path": project.output_path,
        "configuration": project.configuration,
        "configuration_version": project.configuration_version,
        "status": project.status,
        "created_at": _dt(project.created_at),
        "updated_at": _dt(project.updated_at),
    }


def _project_configuration_version_payload(
    version: WorkspaceProjectConfigurationVersion,
) -> dict[str, object]:
    return {
        "id": str(version.id),
        "workspace_id": str(version.workspace_id),
        "project_id": str(version.project_id),
        "version": version.version,
        "configuration": version.configuration,
        "checksum_sha256": version.checksum_sha256,
        "created_by_user_id": stringify_or_none(version.created_by_user_id),
        "change_summary": version.change_summary,
        "created_at": _dt(version.created_at),
    }


def _project_file_payload(binding: WorkspaceProjectFile) -> dict[str, object]:
    return {
        "id": str(binding.id),
        "workspace_id": str(binding.workspace_id),
        "project_id": str(binding.project_id),
        "workspace_file_id": str(binding.workspace_file_id),
        "supersedes_project_file_id": stringify_or_none(binding.supersedes_project_file_id),
        "project_path": binding.project_path,
        "version": binding.version,
        "access_mode": binding.access_mode,
        "status": binding.status,
        "created_at": _dt(binding.created_at),
        "updated_at": _dt(binding.updated_at),
    }


def _project_output_payload(output: WorkspaceProjectOutput) -> dict[str, object]:
    return {
        "id": str(output.id),
        "workspace_id": str(output.workspace_id),
        "project_id": str(output.project_id),
        "project_path": output.project_path,
        "artifact_type": output.artifact_type,
        "content_type": output.content_type,
        "required": output.required,
        "max_bytes": output.max_bytes,
        "status": output.status,
        "created_at": _dt(output.created_at),
        "updated_at": _dt(output.updated_at),
    }


def _memory_entry_payload(entry: WorkspaceMemoryEntry) -> dict[str, object]:
    return {
        "id": str(entry.id),
        "workspace_id": str(entry.workspace_id),
        "created_by_user_id": stringify_or_none(entry.created_by_user_id),
        "created_by_agent_profile_id": stringify_or_none(entry.created_by_agent_profile_id),
        "created_by_agent_run_id": stringify_or_none(entry.created_by_agent_run_id),
        "source_type": entry.source_type,
        "source_id": entry.source_id,
        "memory_layer": entry.memory_layer,
        "scope_type": entry.scope_type,
        "scope_id": entry.scope_id,
        "memory_key": entry.memory_key,
        "entry_type": entry.entry_type,
        "title": entry.title,
        "content": entry.content,
        "tags": entry.tags,
        "visibility_scope": entry.visibility_scope,
        "importance": entry.importance,
        "status": entry.status,
        "revision": entry.revision,
        "content_fingerprint": entry.content_fingerprint,
        "metadata": entry.memory_metadata,
        "last_accessed_at": _dt_or_none(entry.last_accessed_at),
        "expires_at": _dt_or_none(entry.expires_at),
        "archived_at": _dt_or_none(entry.archived_at),
        "access_count": entry.access_count,
        "embedding_status": entry.embedding_status,
        "embedding_generation": entry.embedding_generation,
        "embedding_model": entry.embedding_model,
    }


def _memory_version_payload(version: WorkspaceMemoryVersion) -> dict[str, object]:
    return {
        "id": str(version.id),
        "workspace_id": str(version.workspace_id),
        "memory_entry_id": str(version.memory_entry_id),
        "revision": version.revision,
        "snapshot": version.snapshot,
        "content_fingerprint": version.content_fingerprint,
        "changed_by_user_id": stringify_or_none(version.changed_by_user_id),
        "changed_by_agent_profile_id": stringify_or_none(version.changed_by_agent_profile_id),
        "changed_by_agent_run_id": stringify_or_none(version.changed_by_agent_run_id),
        "change_reason": version.change_reason,
        "created_at": _dt(version.created_at),
    }


def _memory_configuration_payload(
    configuration: WorkspaceMemoryConfiguration,
) -> dict[str, object]:
    return {
        "id": str(configuration.id),
        "workspace_id": str(configuration.workspace_id),
        "embedding_enabled": configuration.embedding_enabled,
        "embedding_model": configuration.embedding_model,
        "embedding_dimensions": configuration.embedding_dimensions,
        "retrieval_policy": configuration.retrieval_policy,
        "lifecycle_policy": configuration.lifecycle_policy,
        "version": configuration.version,
        "updated_by_user_id": stringify_or_none(configuration.updated_by_user_id),
        "credential_binding": "requires_manual_rebind",
        "created_at": _dt(configuration.created_at),
        "updated_at": _dt(configuration.updated_at),
    }


def _dt(value: datetime) -> str:
    return value.isoformat()


def _dt_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
