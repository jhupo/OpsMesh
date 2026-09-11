from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_messages.models import AgentMessage
from backend.app.observability.audit_models import AuditEvent
from backend.app.operations.timeline_constants import (
    TEAM_EXECUTION_ITERATION_ACTION,
    TEAM_RUNTIME_AUDIT_ACTION_PREFIX,
    TEAM_RUNTIME_MESSAGE_TYPES,
)
from backend.app.operations.timeline_models import TimelineEvent, TimelineFilters
from backend.app.operations.timeline_team_context import TeamRuntimeTimelineContext
from backend.app.operations.timeline_utils import apply_time_filters, aware_datetime


class TeamRuntimeMessageTimelineCollector:
    def __init__(self, session: Session) -> None:
        self._session = session

    def agent_messages(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
    ) -> list[TimelineEvent]:
        statement = select(AgentMessage).where(
            AgentMessage.workspace_id == workspace_id,
            AgentMessage.agent_team_id == team_id,
            AgentMessage.message_type.in_(TEAM_RUNTIME_MESSAGE_TYPES),
        )
        statement = apply_time_filters(statement, AgentMessage.created_at, filters)
        messages = self._session.scalars(statement).all()
        return [
            TimelineEvent(
                id=f"agent_message:{message.id}",
                source_type="agent_message",
                event_type=message.message_type,
                occurred_at=aware_datetime(message.created_at),
                resource_id=str(message.id),
                message=message.body,
                metadata={
                    "message_id": str(message.id),
                    "thread_id": str(message.thread_id),
                    "task_id": str(message.task_id) if message.task_id is not None else None,
                    "agent_team_id": str(message.agent_team_id)
                    if message.agent_team_id is not None
                    else None,
                    "sender_agent_profile_id": str(message.sender_agent_profile_id)
                    if message.sender_agent_profile_id is not None
                    else None,
                    "recipient_agent_profile_id": str(message.recipient_agent_profile_id)
                    if message.recipient_agent_profile_id is not None
                    else None,
                    "status": message.status,
                    "payload": dict(message.payload or {}),
                },
            )
            for message in messages
        ]


class TeamRuntimeAuditTimelineCollector:
    def __init__(self, session: Session, context: TeamRuntimeTimelineContext) -> None:
        self._session = session
        self._context = context

    def audit_events(
        self,
        workspace_id: UUID,
        team_id: UUID,
        filters: TimelineFilters,
    ) -> list[TimelineEvent]:
        member_ids = self._context.team_member_ids(workspace_id, team_id)
        target_pairs = [("agent_team", str(team_id))] + [
            ("agent_team_member", str(member_id)) for member_id in member_ids
        ]
        statement = select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.target_type.in_({"agent_team", "agent_team_member"}),
            AuditEvent.target_id.in_({target_id for _target_type, target_id in target_pairs}),
        )
        statement = apply_time_filters(statement, AuditEvent.created_at, filters)
        audit_events = self._session.scalars(statement).all()
        return [
            TimelineEvent(
                id=f"audit_event:{event.id}",
                source_type="audit_event",
                event_type=event.action,
                occurred_at=aware_datetime(event.created_at),
                resource_id=str(event.id),
                message=event.action,
                metadata={
                    "audit_event_id": str(event.id),
                    "actor_type": event.actor_type,
                    "actor_id": event.actor_id,
                    "user_id": str(event.user_id) if event.user_id is not None else None,
                    "agent_run_id": str(event.agent_run_id)
                    if event.agent_run_id is not None
                    else None,
                    "target_type": event.target_type,
                    "target_id": event.target_id,
                    "audit_metadata": dict(event.audit_metadata or {}),
                },
            )
            for event in audit_events
            if (event.target_type, event.target_id) in target_pairs
            if event.action.startswith(TEAM_RUNTIME_AUDIT_ACTION_PREFIX)
            or event.action == "team.member_model_provider.updated"
            or event.action == TEAM_EXECUTION_ITERATION_ACTION
        ]
