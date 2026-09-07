from datetime import datetime

from backend.app.agents.models import AgentProfile
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.capabilities.models import WorkspaceSkillInstall
from backend.app.files.models import WorkspaceFile
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_spaces.models import RuntimeSpace, RuntimeSpaceQuota
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workspaces.models import Workspace


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
        "manager_agent_profile_id": _str_or_none(team.manager_agent_profile_id),
        "runtime_space_id": _str_or_none(team.runtime_space_id),
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
        "reports_to_member_id": _str_or_none(member.reports_to_member_id),
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
        "created_by_user_id": _str_or_none(task.created_by_user_id),
        "created_by_agent_run_id": _str_or_none(task.created_by_agent_run_id),
        "agent_team_id": _str_or_none(task.agent_team_id),
        "runtime_space_id": _str_or_none(task.runtime_space_id),
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
        "assigned_agent_profile_id": _str_or_none(step.assigned_agent_profile_id),
        "runtime_space_id": _str_or_none(step.runtime_space_id),
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
        "task_step_id": _str_or_none(message.task_step_id),
        "agent_run_id": _str_or_none(message.agent_run_id),
        "agent_profile_id": _str_or_none(message.agent_profile_id),
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
        "task_id": _str_or_none(run.task_id),
        "task_step_id": _str_or_none(run.task_step_id),
        "agent_profile_id": _str_or_none(run.agent_profile_id),
        "runtime_id": _str_or_none(run.runtime_id),
        "runtime_space_id": _str_or_none(run.runtime_space_id),
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
        "uploaded_by_user_id": _str_or_none(file.uploaded_by_user_id),
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": file.size_bytes,
        "checksum_sha256": file.checksum_sha256,
        "status": file.status,
        "metadata": file.file_metadata,
        "created_at": _dt(file.created_at),
        "updated_at": _dt(file.updated_at),
    }


def _artifact_payload(artifact: Artifact) -> dict[str, object]:
    return {
        "id": str(artifact.id),
        "workspace_id": str(artifact.workspace_id),
        "task_id": _str_or_none(artifact.task_id),
        "agent_run_id": _str_or_none(artifact.agent_run_id),
        "task_step_id": _str_or_none(artifact.task_step_id),
        "agent_profile_id": _str_or_none(artifact.agent_profile_id),
        "supersedes_artifact_id": _str_or_none(artifact.supersedes_artifact_id),
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
        "created_by_user_id": _str_or_none(runtime_space.created_by_user_id),
        "default_runtime_template_id": _str_or_none(runtime_space.default_runtime_template_id),
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
        "installed_by_user_id": _str_or_none(install.installed_by_user_id),
        "installed_key": install.installed_key,
        "installed_name": install.installed_name,
        "installed_version": install.installed_version,
        "installed_description": install.installed_description,
        "installed_capability_keys": install.installed_capability_keys,
        "installed_manifest": install.installed_manifest,
        "source_owner_workspace_id": _str_or_none(install.source_owner_workspace_id),
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
        "user_id": _str_or_none(event.user_id),
        "agent_run_id": _str_or_none(event.agent_run_id),
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "metadata": event.audit_metadata,
        "created_at": _dt(event.created_at),
    }


def _dt(value: datetime) -> str:
    return value.isoformat()


def _dt_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _str_or_none(value: object | None) -> str | None:
    return str(value) if value is not None else None

