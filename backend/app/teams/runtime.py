from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agent_runtime.sessions import (
    ACTIVE_SESSION_STATUS,
    PersistentAgentSession,
    PersistentAgentSessionRef,
)
from backend.app.agents.models import AgentProfile
from backend.app.audit.service import AuditService
from backend.app.runtime_manager.contracts import RuntimeLimits
from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.runtime_spaces.models import RuntimeSpace
from backend.app.runtimes.models import RuntimeTemplate, WorkspaceRuntime
from backend.app.security.redaction import redact_sensitive_payload, redact_sensitive_text
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.teams.operating_context import TeamOperatingContextService

TEAM_RUNTIME_RUNNING = "running"
TEAM_RUNTIME_PAUSED = "paused"
TEAM_RUNTIME_STOPPED = "stopped"
TEAM_RUNTIME_STATUS_KEY = "team_runtime"
TEAM_RUNTIME_THREAD_KEY = "team_runtime_thread_id"
TEAM_RUNTIME_SESSION_SCOPE = "team_runtime"
TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY = "workspace_runtime_id"
TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS = 300


@dataclass(frozen=True)
class TeamRuntimeState:
    workspace_id: UUID
    team_id: UUID
    generated_at: datetime
    status: str
    team_session_id: UUID | None
    team_session_key: str | None
    thread_id: UUID | None
    workspace_runtime_id: UUID | None
    runtime_status: str | None
    runtime_space_id: UUID | None
    last_iteration: dict[str, object] | None
    last_message_at: datetime | None
    runtime_health: str
    member_session_count: int
    member_agent_ids: list[UUID]
    operating_policy: dict[str, object]
    memory_summary: dict[str, object]
    metadata: dict[str, object]


class TeamRuntimeService:
    """Persistent controls and shared context for a team/company runtime."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_state(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        initialize: bool = False,
    ) -> TeamRuntimeState | None:
        team = self._team(workspace_id, team_id)
        if team is None:
            return None
        if initialize:
            thread = self._get_or_create_thread(team)
            team_session = self._get_or_create_team_session(team)
            member_sessions = self.ensure_member_sessions(team)
            self._session.flush()
        else:
            thread = self._existing_thread(team)
            team_session = self._existing_team_session(team)
            member_sessions = self._existing_member_sessions(team)
        return self._state(team, team_session, thread, member_sessions)

    def ensure_thread(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
    ) -> AgentMessageThread | None:
        team = self._team(workspace_id, team_id)
        if team is None:
            return None
        thread = self._get_or_create_thread(team)
        self._session.flush()
        return thread

    def start(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeControlService | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
        return self._transition(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=TEAM_RUNTIME_RUNNING,
            event_type="team.runtime.started",
            runtime_control=runtime_control,
            reason=reason,
            metadata=metadata,
        )

    def pause(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeControlService | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
        return self._transition(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=TEAM_RUNTIME_PAUSED,
            event_type="team.runtime.paused",
            runtime_control=runtime_control,
            reason=reason,
            metadata=metadata,
        )

    def resume(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeControlService | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
        return self._transition(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=TEAM_RUNTIME_RUNNING,
            event_type="team.runtime.resumed",
            runtime_control=runtime_control,
            reason=reason,
            metadata=metadata,
        )

    def stop(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeControlService | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
        return self._transition(
            workspace_id=workspace_id,
            team_id=team_id,
            actor_user_id=actor_user_id,
            status=TEAM_RUNTIME_STOPPED,
            event_type="team.runtime.stopped",
            runtime_control=runtime_control,
            reason=reason,
            metadata=metadata,
        )

    def bind_runtime(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        workspace_runtime_id: UUID,
        actor_user_id: UUID,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
        team = self._team(workspace_id, team_id)
        if team is None:
            return None
        runtime = self._runtime(workspace_id, workspace_runtime_id)
        if runtime is None:
            raise ValueError("Workspace runtime not found")
        if team.runtime_space_id is not None and runtime.runtime_space_id != team.runtime_space_id:
            raise ValueError("Workspace runtime is not in the team's runtime space")

        policy = dict(team.default_task_policy or {})
        runtime_metadata = dict(policy.get(TEAM_RUNTIME_STATUS_KEY) or {})
        runtime_metadata[TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY] = str(runtime.id)
        runtime_metadata["runtime_bound_at"] = datetime.now(UTC).isoformat()
        runtime_metadata["updated_by_user_id"] = str(actor_user_id)
        if reason:
            runtime_metadata["reason"] = reason
        if metadata:
            runtime_metadata["metadata"] = dict(metadata)
        policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
        team.default_task_policy = policy

        capabilities = dict(runtime.capabilities or {})
        capabilities["team_runtime"] = {
            "workspace_id": str(team.workspace_id),
            "team_id": str(team.id),
            "team_name": team.name,
            "bound_at": runtime_metadata["runtime_bound_at"],
        }
        runtime.capabilities = capabilities

        thread = self._get_or_create_thread(team)
        team_session = self._get_or_create_team_session(team)
        member_sessions = self.ensure_member_sessions(team)
        self._append_runtime_message(
            team=team,
            thread=thread,
            actor_user_id=actor_user_id,
            message_type="team.runtime.bound",
            body=reason or "Workspace runtime bound to team runtime",
            payload=runtime_metadata,
        )
        AuditService(self._session).record_user_action(
            workspace_id=team.workspace_id,
            user_id=actor_user_id,
            action="team.runtime.bound",
            target_type="agent_team",
            target_id=team.id,
            metadata=runtime_metadata,
        )
        self._session.commit()
        self._session.refresh(team)
        return self._state(team, team_session, thread, member_sessions)

    def ensure_workspace_runtime(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeControlService,
        template_id: UUID | None = None,
        name: str | None = None,
        limits: RuntimeLimits | None = None,
        network_disabled: bool = True,
        start: bool = True,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
        team = self._team(workspace_id, team_id)
        if team is None:
            return None

        runtime = self._bound_runtime(team)
        created = False
        started = False
        resolved_template_id = template_id
        if runtime is None:
            resolved_template_id = self._resolve_template_id(team, template_id)
            if resolved_template_id is None:
                raise ValueError("No active runtime template available")
            runtime = runtime_control.create_runtime(
                workspace_id=team.workspace_id,
                template_id=resolved_template_id,
                name=name or f"{team.name} team runtime",
                limits=limits,
                runtime_space_id=team.runtime_space_id,
                network_disabled=network_disabled,
            )
            if runtime is None:
                raise ValueError("Runtime template not found")
            created = True

        if start and (
            runtime.status != "running" or runtime.connection_status in {"offline", "error"}
        ):
            runtime = runtime_control.start_runtime(team.workspace_id, runtime.id)
            if runtime is None:
                raise ValueError("Workspace runtime not found")
            started = True

        policy = dict(team.default_task_policy or {})
        runtime_metadata = dict(policy.get(TEAM_RUNTIME_STATUS_KEY) or {})
        ensured_at = datetime.now(UTC).isoformat()
        status = (
            TEAM_RUNTIME_RUNNING
            if start
            else runtime_metadata.get("status", TEAM_RUNTIME_STOPPED)
        )
        runtime_metadata.update(
            {
                "status": status,
                TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY: str(runtime.id),
                "runtime_bound_at": runtime_metadata.get("runtime_bound_at") or ensured_at,
                "runtime_ensured_at": ensured_at,
                "runtime_created": created,
                "runtime_started": started,
                "updated_by_user_id": str(actor_user_id),
            }
        )
        if resolved_template_id is not None:
            runtime_metadata["runtime_template_id"] = str(resolved_template_id)
        if reason:
            runtime_metadata["reason"] = reason
        if metadata:
            runtime_metadata["metadata"] = dict(metadata)
        policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
        team.default_task_policy = policy

        capabilities = dict(runtime.capabilities or {})
        capabilities["team_runtime"] = {
            "workspace_id": str(team.workspace_id),
            "team_id": str(team.id),
            "team_name": team.name,
            "bound_at": runtime_metadata["runtime_bound_at"],
            "ensured_at": ensured_at,
        }
        runtime.capabilities = capabilities

        thread = self._get_or_create_thread(team)
        team_session = self._get_or_create_team_session(team)
        member_sessions = self.ensure_member_sessions(team)
        self._append_runtime_message(
            team=team,
            thread=thread,
            actor_user_id=actor_user_id,
            message_type="team.runtime.ensured",
            body=reason or "Workspace runtime ensured for team runtime",
            payload=runtime_metadata,
        )
        AuditService(self._session).record_user_action(
            workspace_id=team.workspace_id,
            user_id=actor_user_id,
            action="team.runtime.ensured",
            target_type="agent_team",
            target_id=team.id,
            metadata=runtime_metadata,
        )
        self._session.commit()
        self._session.refresh(team)
        return self._state(team, team_session, thread, member_sessions)

    def continue_runtime(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        runtime_control: RuntimeControlService | None = None,
        instruction: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> TeamRuntimeState | None:
        team = self._team(workspace_id, team_id)
        if team is None:
            return None
        state = self._transition_team(
            team=team,
            actor_user_id=actor_user_id,
            status=TEAM_RUNTIME_RUNNING,
            event_type="team.runtime.continued",
            runtime_control=runtime_control,
            reason=instruction,
            metadata=metadata,
        )
        if state is None:
            return None
        return state

    def record_iteration(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        summary: dict[str, object],
    ) -> None:
        team = self._team(workspace_id, team_id)
        if team is None:
            return
        policy = dict(team.default_task_policy or {})
        runtime_metadata = dict(policy.get(TEAM_RUNTIME_STATUS_KEY) or {})
        recorded_at = datetime.now(UTC).isoformat()
        iteration_count = _int(runtime_metadata.get("iteration_count")) + 1
        last_iteration: dict[str, object] = {
            "iteration": iteration_count,
            "status": status,
            "summary": dict(summary),
            "recorded_at": recorded_at,
            "actor_user_id": str(actor_user_id),
        }
        runtime_metadata.update(
            {
                "last_iteration": last_iteration,
                "iteration_count": iteration_count,
                "last_heartbeat_at": recorded_at,
                "heartbeat_status": status,
            }
        )
        runtime_metadata.pop("last_worker_failure", None)
        policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
        team.default_task_policy = policy
        thread = self._get_or_create_thread(team)
        self._append_runtime_message(
            team=team,
            thread=thread,
            actor_user_id=actor_user_id,
            message_type="runtime_iteration",
            body=f"Team execution loop iteration: {status}",
            payload={"status": status, "summary": summary, "iteration": last_iteration},
        )
        self._session.flush()

    def record_worker_failure(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        worker_id: str,
        queue_name: str,
        job_id: UUID,
        status: str,
        attempt: int,
        max_attempts: int,
        will_retry: bool,
        error: BaseException,
        actor_user_id: UUID | None = None,
        routing: dict[str, object] | None = None,
        trace_metadata: dict[str, object] | None = None,
    ) -> None:
        team = self._team(workspace_id, team_id)
        if team is None:
            return
        policy = dict(team.default_task_policy or {})
        runtime_metadata = dict(policy.get(TEAM_RUNTIME_STATUS_KEY) or {})
        recorded_at = datetime.now(UTC).isoformat()
        failure = {
            "status": status,
            "recorded_at": recorded_at,
            "worker_id": worker_id,
            "queue_name": queue_name,
            "job_id": str(job_id),
            "attempt": attempt,
            "max_attempts": max_attempts,
            "will_retry": will_retry,
            "error_type": type(error).__name__,
            "error": redact_sensitive_text(str(error)),
            "routing": redact_sensitive_payload(dict(routing or {})),
            "trace": redact_sensitive_payload(dict(trace_metadata or {})),
        }
        runtime_metadata.update(
            {
                "last_worker_failure": failure,
                "heartbeat_status": status,
                "last_heartbeat_at": recorded_at,
            }
        )
        policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
        team.default_task_policy = policy
        thread = self._get_or_create_thread(team)
        self._append_runtime_message(
            team=team,
            thread=thread,
            actor_user_id=actor_user_id,
            message_type=f"team.runtime.worker_{status}",
            body=f"Team execution loop worker {status}",
            payload={"status": status, "worker_failure": failure},
        )
        self._session.flush()

    def ensure_member_sessions(self, team: AgentTeam) -> list[PersistentAgentSession]:
        members = self._active_members(team.workspace_id, team.id)
        sessions: list[PersistentAgentSession] = []
        for member in members:
            sessions.append(
                self._get_or_create_session(
                    workspace_id=team.workspace_id,
                    session_key=_member_session_key(
                        team.workspace_id,
                        team.id,
                        member.agent_profile_id,
                    ),
                    scope_type="team_agent",
                    scope_id=f"{team.id}:{member.agent_profile_id}",
                    agent_profile_id=member.agent_profile_id,
                    agent_team_id=team.id,
                    metadata={
                        "source": "team_runtime",
                        "team_role": member.team_role,
                        "department": member.department,
                    },
                )
            )
        return sessions

    def _transition(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        status: str,
        event_type: str,
        runtime_control: RuntimeControlService | None,
        reason: str | None,
        metadata: dict[str, object] | None,
    ) -> TeamRuntimeState | None:
        team = self._team(workspace_id, team_id)
        if team is None:
            return None
        return self._transition_team(
            team=team,
            actor_user_id=actor_user_id,
            status=status,
            event_type=event_type,
            runtime_control=runtime_control,
            reason=reason,
            metadata=metadata,
        )

    def _transition_team(
        self,
        *,
        team: AgentTeam,
        actor_user_id: UUID,
        status: str,
        event_type: str,
        runtime_control: RuntimeControlService | None,
        reason: str | None,
        metadata: dict[str, object] | None,
    ) -> TeamRuntimeState | None:
        policy = dict(team.default_task_policy or {})
        runtime_metadata = dict(policy.get(TEAM_RUNTIME_STATUS_KEY) or {})
        updated_at = datetime.now(UTC).isoformat()
        workspace_runtime_control = self._apply_bound_runtime_control(
            team=team,
            desired_status=status,
            runtime_control=runtime_control,
            updated_at=updated_at,
        )
        runtime_metadata.update(
            {
                "status": status,
                "updated_at": updated_at,
                "updated_by_user_id": str(actor_user_id),
            }
        )
        had_worker_failure = "last_worker_failure" in runtime_metadata
        if status == TEAM_RUNTIME_RUNNING:
            runtime_metadata.pop("last_worker_failure", None)
            if had_worker_failure and event_type == "team.runtime.continued":
                runtime_metadata["heartbeat_status"] = TEAM_RUNTIME_RUNNING
                runtime_metadata["last_heartbeat_at"] = updated_at
        if workspace_runtime_control:
            runtime_metadata["workspace_runtime_control"] = workspace_runtime_control
        if event_type == "team.runtime.continued":
            runtime_metadata["continued_at"] = updated_at
            last_iteration = _dict_or_none(runtime_metadata.get("last_iteration"))
            if last_iteration is not None:
                runtime_metadata["continued_from_iteration"] = last_iteration
        if reason:
            runtime_metadata["reason"] = reason
        if metadata:
            runtime_metadata["metadata"] = dict(metadata)
        policy[TEAM_RUNTIME_STATUS_KEY] = runtime_metadata
        team.default_task_policy = policy

        thread = self._get_or_create_thread(team)
        team_session = self._get_or_create_team_session(team)
        member_sessions = self.ensure_member_sessions(team)
        self._append_runtime_message(
            team=team,
            thread=thread,
            actor_user_id=actor_user_id,
            message_type=event_type,
            body=reason or event_type,
            payload=runtime_metadata,
        )
        AuditService(self._session).record_user_action(
            workspace_id=team.workspace_id,
            user_id=actor_user_id,
            action=event_type,
            target_type="agent_team",
            target_id=team.id,
            metadata=runtime_metadata,
        )
        self._session.commit()
        self._session.refresh(team)
        return self._state(team, team_session, thread, member_sessions)

    def _apply_bound_runtime_control(
        self,
        *,
        team: AgentTeam,
        desired_status: str,
        runtime_control: RuntimeControlService | None,
        updated_at: str,
    ) -> dict[str, object]:
        runtime = self._bound_runtime(team)
        if runtime is None:
            return {}

        control: dict[str, object] = {
            TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY: str(runtime.id),
            "requested_status": desired_status,
            "runtime_status_before": runtime.status,
            "controlled_at": updated_at,
        }
        if desired_status == TEAM_RUNTIME_RUNNING:
            control["action"] = "start"
            if runtime.status == "running":
                control.update(
                    {
                        "mode": "already_running",
                        "runtime_status_after": runtime.status,
                    }
                )
                return control
            if runtime_control is None:
                control.update(
                    {
                        "mode": "logical_only",
                        "reason": "runtime_control_unavailable",
                    }
                )
                return control
            started_runtime = runtime_control.start_runtime(team.workspace_id, runtime.id)
            if started_runtime is None:
                raise ValueError("Workspace runtime not found")
            control.update(
                {
                    "mode": "lifecycle",
                    "runtime_status_after": started_runtime.status,
                }
            )
            return control

        if desired_status in {TEAM_RUNTIME_PAUSED, TEAM_RUNTIME_STOPPED}:
            control["action"] = "stop"
            if runtime.status != "running":
                control.update(
                    {
                        "mode": "already_stopped",
                        "runtime_status_after": runtime.status,
                    }
                )
                return control
            if runtime_control is None:
                control.update(
                    {
                        "mode": "logical_only",
                        "reason": "runtime_control_unavailable",
                    }
                )
                return control
            stopped_runtime = runtime_control.stop_runtime(team.workspace_id, runtime.id)
            if stopped_runtime is None:
                raise ValueError("Workspace runtime not found")
            control.update(
                {
                    "mode": "lifecycle",
                    "runtime_status_after": stopped_runtime.status,
                }
            )
            return control

        return control

    def _team(self, workspace_id: UUID, team_id: UUID) -> AgentTeam | None:
        return self._session.scalar(
            select(AgentTeam).where(
                AgentTeam.workspace_id == workspace_id,
                AgentTeam.id == team_id,
            )
        )

    def _runtime(self, workspace_id: UUID, workspace_runtime_id: UUID) -> WorkspaceRuntime | None:
        return self._session.scalar(
            select(WorkspaceRuntime).where(
                WorkspaceRuntime.workspace_id == workspace_id,
                WorkspaceRuntime.id == workspace_runtime_id,
                WorkspaceRuntime.status != "deleted",
            )
        )

    def _bound_runtime(self, team: AgentTeam) -> WorkspaceRuntime | None:
        runtime_id = team_bound_runtime_id(team)
        if runtime_id is None:
            return None
        return self._runtime(team.workspace_id, runtime_id)

    def _resolve_template_id(self, team: AgentTeam, template_id: UUID | None) -> UUID | None:
        if template_id is not None:
            return template_id
        policy = team.default_task_policy if isinstance(team.default_task_policy, dict) else {}
        runtime_policy = policy.get(TEAM_RUNTIME_STATUS_KEY)
        if isinstance(runtime_policy, dict):
            policy_template_id = _uuid_or_none(runtime_policy.get("runtime_template_id"))
            if policy_template_id is not None:
                return policy_template_id
        if team.runtime_space_id is not None:
            runtime_space = self._session.scalar(
                select(RuntimeSpace).where(
                    RuntimeSpace.workspace_id == team.workspace_id,
                    RuntimeSpace.id == team.runtime_space_id,
                )
            )
            if runtime_space is not None and runtime_space.default_runtime_template_id is not None:
                return runtime_space.default_runtime_template_id
        return self._session.scalar(
            select(RuntimeTemplate.id)
            .where(RuntimeTemplate.status == "active")
            .order_by(RuntimeTemplate.created_at.desc(), RuntimeTemplate.name.asc())
            .limit(1)
        )

    def _active_members(self, workspace_id: UUID, team_id: UUID) -> list[AgentTeamMember]:
        return list(
            self._session.scalars(
                select(AgentTeamMember)
                .where(
                    AgentTeamMember.workspace_id == workspace_id,
                    AgentTeamMember.agent_team_id == team_id,
                    AgentTeamMember.status == "active",
                )
                .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            )
        )

    def _get_or_create_team_session(self, team: AgentTeam) -> PersistentAgentSession:
        return self._get_or_create_session(
            workspace_id=team.workspace_id,
            session_key=_team_session_key(team.workspace_id, team.id),
            scope_type=TEAM_RUNTIME_SESSION_SCOPE,
            scope_id=str(team.id),
            agent_profile_id=team.manager_agent_profile_id,
            agent_team_id=team.id,
            metadata={"source": "team_runtime", "team_name": team.name},
        )

    def _existing_team_session(self, team: AgentTeam) -> PersistentAgentSession | None:
        return self._session.scalar(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == team.workspace_id,
                PersistentAgentSession.session_key == _team_session_key(
                    team.workspace_id,
                    team.id,
                ),
            )
        )

    def _existing_member_sessions(self, team: AgentTeam) -> list[PersistentAgentSession]:
        return list(
            self._session.scalars(
                select(PersistentAgentSession)
                .where(
                    PersistentAgentSession.workspace_id == team.workspace_id,
                    PersistentAgentSession.agent_team_id == team.id,
                    PersistentAgentSession.scope_type == "team_agent",
                )
                .order_by(PersistentAgentSession.updated_at.desc(), PersistentAgentSession.id)
            )
        )

    def _get_or_create_session(
        self,
        *,
        workspace_id: UUID,
        session_key: str,
        scope_type: str,
        scope_id: str,
        agent_profile_id: UUID | None,
        agent_team_id: UUID,
        metadata: dict[str, object],
    ) -> PersistentAgentSession:
        existing = self._session.scalar(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == workspace_id,
                PersistentAgentSession.session_key == session_key,
            )
        )
        if existing is not None:
            return existing
        created = PersistentAgentSession(
            workspace_id=workspace_id,
            session_key=session_key,
            scope_type=scope_type,
            scope_id=scope_id,
            agent_profile_id=agent_profile_id,
            agent_team_id=agent_team_id,
            session_metadata=metadata,
            status=ACTIVE_SESSION_STATUS,
        )
        self._session.add(created)
        self._session.flush([created])
        return created

    def _get_or_create_thread(self, team: AgentTeam) -> AgentMessageThread:
        policy = dict(team.default_task_policy or {})
        thread_id = _uuid_or_none(policy.get(TEAM_RUNTIME_THREAD_KEY))
        if thread_id is not None:
            thread = self._session.scalar(
                select(AgentMessageThread).where(
                    AgentMessageThread.workspace_id == team.workspace_id,
                    AgentMessageThread.id == thread_id,
                    AgentMessageThread.agent_team_id == team.id,
                )
            )
            if thread is not None:
                return thread
        thread = AgentMessageThread(
            workspace_id=team.workspace_id,
            agent_team_id=team.id,
            subject=f"{team.name} runtime",
            status="active",
        )
        self._session.add(thread)
        self._session.flush([thread])
        policy[TEAM_RUNTIME_THREAD_KEY] = str(thread.id)
        team.default_task_policy = policy
        return thread

    def _existing_thread(self, team: AgentTeam) -> AgentMessageThread | None:
        policy = dict(team.default_task_policy or {})
        thread_id = _uuid_or_none(policy.get(TEAM_RUNTIME_THREAD_KEY))
        if thread_id is None:
            return None
        return self._session.scalar(
            select(AgentMessageThread).where(
                AgentMessageThread.workspace_id == team.workspace_id,
                AgentMessageThread.id == thread_id,
                AgentMessageThread.agent_team_id == team.id,
            )
        )

    def _append_runtime_message(
        self,
        *,
        team: AgentTeam,
        thread: AgentMessageThread,
        actor_user_id: UUID | None,
        message_type: str,
        body: str,
        payload: dict[str, object],
    ) -> AgentMessage:
        sender_agent_id = team.manager_agent_profile_id or self._first_agent_id(team)
        message_payload = {**payload, "team_id": str(team.id)}
        if actor_user_id is not None:
            message_payload["actor_user_id"] = str(actor_user_id)
        message = AgentMessage(
            workspace_id=team.workspace_id,
            thread_id=thread.id,
            agent_team_id=team.id,
            sender_agent_profile_id=sender_agent_id,
            recipient_agent_profile_id=sender_agent_id,
            message_type=message_type,
            body=body,
            payload=message_payload,
            status="sent",
        )
        self._session.add(message)
        self._session.flush([message])
        thread.updated_at = message.created_at
        return message

    def _first_agent_id(self, team: AgentTeam) -> UUID | None:
        return self._session.scalar(
            select(AgentProfile.id)
            .join(AgentTeamMember, AgentTeamMember.agent_profile_id == AgentProfile.id)
            .where(
                AgentTeamMember.workspace_id == team.workspace_id,
                AgentTeamMember.agent_team_id == team.id,
                AgentTeamMember.status == "active",
            )
            .order_by(AgentTeamMember.order_index.asc(), AgentTeamMember.id.asc())
            .limit(1)
        )

    def _state(
        self,
        team: AgentTeam,
        team_session: PersistentAgentSession | None,
        thread: AgentMessageThread | None,
        member_sessions: list[PersistentAgentSession],
    ) -> TeamRuntimeState:
        runtime_metadata = dict((team.default_task_policy or {}).get(TEAM_RUNTIME_STATUS_KEY) or {})
        status = str(runtime_metadata.get("status") or TEAM_RUNTIME_STOPPED)
        workspace_runtime_id = _uuid_or_none(
            runtime_metadata.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY)
        )
        runtime = (
            self._runtime(team.workspace_id, workspace_runtime_id)
            if workspace_runtime_id is not None
            else None
        )
        generated_at = datetime.now(UTC)
        last_iteration_message = (
            self._last_iteration_message(team, thread) if thread is not None else None
        )
        last_message_at = self._last_message_at(team, thread) if thread is not None else None
        operating_context = TeamOperatingContextService(self._session)
        return TeamRuntimeState(
            workspace_id=team.workspace_id,
            team_id=team.id,
            generated_at=generated_at,
            status=status,
            team_session_id=team_session.id if team_session is not None else None,
            team_session_key=team_session.session_key if team_session is not None else None,
            thread_id=thread.id if thread is not None else None,
            workspace_runtime_id=runtime.id if runtime is not None else None,
            runtime_status=runtime.status if runtime is not None else None,
            runtime_space_id=(
                runtime.runtime_space_id if runtime is not None else team.runtime_space_id
            ),
            last_iteration=_last_iteration(runtime_metadata, last_iteration_message),
            last_message_at=last_message_at,
            runtime_health=_runtime_health(
                status=status,
                runtime=runtime,
                metadata=runtime_metadata,
                generated_at=generated_at,
            ),
            member_session_count=len(member_sessions),
            member_agent_ids=[
                session.agent_profile_id
                for session in member_sessions
                if session.agent_profile_id is not None
            ],
            operating_policy=operating_context.operating_policy(team=team),
            memory_summary=operating_context.memory_summary(team=team),
            metadata=runtime_metadata,
        )

    def _last_message_at(self, team: AgentTeam, thread: AgentMessageThread) -> datetime | None:
        return self._session.scalar(
            select(AgentMessage.created_at)
            .where(
                AgentMessage.workspace_id == thread.workspace_id,
                AgentMessage.thread_id == thread.id,
                AgentMessage.agent_team_id == team.id,
            )
            .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
            .limit(1)
        )

    def _last_iteration_message(
        self,
        team: AgentTeam,
        thread: AgentMessageThread,
    ) -> AgentMessage | None:
        return self._session.scalar(
            select(AgentMessage)
            .where(
                AgentMessage.workspace_id == thread.workspace_id,
                AgentMessage.thread_id == thread.id,
                AgentMessage.agent_team_id == team.id,
                AgentMessage.message_type == "runtime_iteration",
            )
            .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
            .limit(1)
        )


def team_runtime_ref(workspace_id: UUID, team_id: UUID) -> PersistentAgentSessionRef:
    return PersistentAgentSessionRef(
        session_key=_team_session_key(workspace_id, team_id),
        workspace_id=workspace_id,
        scope_type=TEAM_RUNTIME_SESSION_SCOPE,
        scope_id=str(team_id),
    )


def team_bound_runtime_id(team: AgentTeam) -> UUID | None:
    policy = team.default_task_policy if isinstance(team.default_task_policy, dict) else {}
    runtime = policy.get(TEAM_RUNTIME_STATUS_KEY)
    if not isinstance(runtime, dict):
        return None
    return _uuid_or_none(runtime.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY))


def _team_session_key(workspace_id: UUID, team_id: UUID) -> str:
    return f"{workspace_id}:{TEAM_RUNTIME_SESSION_SCOPE}:{team_id}"


def _member_session_key(workspace_id: UUID, team_id: UUID, agent_profile_id: UUID) -> str:
    return f"{workspace_id}:team_agent:{team_id}:{agent_profile_id}"


def _uuid_or_none(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def _dict_or_none(value: object) -> dict[str, object] | None:
    return dict(value) if isinstance(value, dict) else None


def _last_iteration(
    runtime_metadata: dict[str, object],
    message: AgentMessage | None,
) -> dict[str, object] | None:
    metadata_iteration = _dict_or_none(runtime_metadata.get("last_iteration"))
    if metadata_iteration is not None:
        return metadata_iteration
    if message is None:
        return None
    payload = message.payload if isinstance(message.payload, dict) else {}
    message_iteration = _dict_or_none(payload.get("iteration"))
    if message_iteration is not None:
        return message_iteration
    return {
        "status": str(payload.get("status") or "unknown"),
        "summary": _dict_or_none(payload.get("summary")) or {},
        "recorded_at": message.created_at.isoformat(),
    }


def _runtime_health(
    *,
    status: str,
    runtime: WorkspaceRuntime | None,
    metadata: dict[str, object],
    generated_at: datetime,
) -> str:
    if status == TEAM_RUNTIME_STOPPED:
        return "stopped"
    if status == TEAM_RUNTIME_PAUSED:
        return "paused"
    if runtime is not None and (
        runtime.status != "running" or runtime.connection_status in {"offline", "error"}
    ):
        return "degraded"
    if metadata.get(TEAM_RUNTIME_WORKSPACE_RUNTIME_ID_KEY) is not None and runtime is None:
        return "degraded"

    last_heartbeat_at = _datetime_or_none(metadata.get("last_heartbeat_at"))
    if last_heartbeat_at is None:
        return "starting"
    if generated_at - last_heartbeat_at > timedelta(
        seconds=TEAM_RUNTIME_HEARTBEAT_STALE_AFTER_SECONDS
    ):
        return "stale"
    if metadata.get("heartbeat_status") == "skipped":
        return "degraded"
    if _last_worker_failure_active(metadata):
        return "degraded"
    return "healthy"


def _last_worker_failure_active(metadata: dict[str, object]) -> bool:
    failure = metadata.get("last_worker_failure")
    if not isinstance(failure, dict):
        return False
    status = failure.get("status")
    return status in {"retrying", "failed"}


def _datetime_or_none(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0
