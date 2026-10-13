from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import String, cast, func, literal, select, union_all
from sqlalchemy.orm import Session

from opsmesh.agents.messages.models import AgentMessage
from opsmesh.capabilities.mcp.models import MCPToolCallLog
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.audit.service import AuditService
from opsmesh.orchestration.runs.evidence import public_run_event_metadata
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.runtime.instances.models import RuntimeEvent
from opsmesh.shared.utils import ensure_aware_utc
from opsmesh.teams.execution.models import TeamOrchestrationRequest
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember
from opsmesh.teams.observation.schemas import (
    TeamTimelineEvent,
    TeamTimelineResponse,
    TeamTimelineSummary,
)
from opsmesh.teams.sessions.refs import team_bound_runtime_id


@dataclass(frozen=True)
class TimelineFilters:
    limit: int = 50
    offset: int = 0
    source_type: str | None = None
    event_type: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    include_runs: bool = False


class TeamTimelineService:
    """Query and paginate persisted evidence in PostgreSQL, with no queue snapshot."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def timeline(
        self, *, workspace_id: UUID, team_id: UUID, filters: TimelineFilters
    ) -> TeamTimelineResponse | None:
        if filters.limit < 1 or filters.limit > 200 or filters.offset < 0:
            raise ValueError("Invalid timeline pagination")
        if (
            filters.since
            and filters.until
            and ensure_aware_utc(filters.since) > ensure_aware_utc(filters.until)
        ):
            raise ValueError("since must be before until")
        team = self.session.scalar(
            select(AgentTeam).where(AgentTeam.workspace_id == workspace_id, AgentTeam.id == team_id)
        )
        if team is None:
            return None
        task_ids = select(Task.id).where(
            Task.workspace_id == workspace_id, Task.agent_team_id == team_id
        )
        run_ids = select(AgentRun.id).where(
            AgentRun.workspace_id == workspace_id, AgentRun.task_id.in_(task_ids)
        )
        member_ids = select(cast(AgentTeamMember.id, String)).where(
            AgentTeamMember.workspace_id == workspace_id, AgentTeamMember.agent_team_id == team_id
        )
        statements = [
            select(
                literal("agent_message").label("source_type"),
                cast(AgentMessage.id, String).label("resource_id"),
                AgentMessage.message_type.label("event_type"),
                AgentMessage.created_at.label("occurred_at"),
                AgentMessage.body.label("message"),
                AgentMessage.payload.label("metadata"),
            ).where(
                AgentMessage.workspace_id == workspace_id, AgentMessage.agent_team_id == team_id
            ),
            select(
                literal("audit_event"),
                cast(AuditEvent.id, String),
                AuditEvent.action,
                AuditEvent.created_at,
                AuditEvent.action,
                func.jsonb_build_object(
                    "target_type",
                    AuditEvent.target_type,
                    "target_id",
                    AuditEvent.target_id,
                    "audit_metadata",
                    AuditEvent.audit_metadata,
                ),
            ).where(
                AuditEvent.workspace_id == workspace_id,
                ((AuditEvent.target_type == "agent_team") & (AuditEvent.target_id == str(team_id)))
                | (
                    (AuditEvent.target_type == "agent_team_member")
                    & AuditEvent.target_id.in_(member_ids)
                ),
            ),
            select(
                literal("advancement"),
                cast(TeamOrchestrationRequest.id, String),
                literal("team.advancement.") + TeamOrchestrationRequest.status,
                TeamOrchestrationRequest.updated_at,
                TeamOrchestrationRequest.status,
                func.jsonb_build_object(
                    "result",
                    TeamOrchestrationRequest.result,
                    "last_error_code",
                    TeamOrchestrationRequest.last_error_code,
                ),
            ).where(
                TeamOrchestrationRequest.workspace_id == workspace_id,
                TeamOrchestrationRequest.team_id == team_id,
            ),
            select(
                literal("task_step"),
                cast(TaskStep.id, String),
                literal("team.runtime.step.scheduling_blocked"),
                TaskStep.updated_at,
                literal("Team runtime step scheduling blocked: ")
                + func.coalesce(TaskStep.dependencies["blocked_reason"].astext, "unknown"),
                func.jsonb_build_object(
                    "task_id",
                    cast(TaskStep.task_id, String),
                    "task_step_id",
                    cast(TaskStep.id, String),
                    "assigned_agent_profile_id",
                    cast(TaskStep.assigned_agent_profile_id, String),
                    "blocked_reason",
                    TaskStep.dependencies["blocked_reason"],
                    "blocked_details",
                    TaskStep.dependencies["blocked_details"],
                ),
            ).where(
                TaskStep.workspace_id == workspace_id,
                TaskStep.task_id.in_(task_ids),
                TaskStep.dependencies["scheduling_status"].astext == "blocked",
            ),
            select(
                literal("mcp_tool_call"),
                cast(MCPToolCallLog.id, String),
                literal("mcp.tool.") + MCPToolCallLog.status,
                MCPToolCallLog.created_at,
                MCPToolCallLog.tool_name,
                func.jsonb_build_object(
                    "agent_run_id",
                    cast(MCPToolCallLog.agent_run_id, String),
                    "tool_call_id",
                    MCPToolCallLog.tool_call_id,
                    "status",
                    MCPToolCallLog.status,
                    "error_code",
                    MCPToolCallLog.error_code,
                ),
            ).where(
                MCPToolCallLog.workspace_id == workspace_id,
                MCPToolCallLog.agent_run_id.in_(run_ids),
            ),
        ]
        cutoff = AuditService(self.session).retention_cutoff()
        if cutoff:
            statements[1] = statements[1].where(AuditEvent.created_at >= cutoff)
        runtime_id = team_bound_runtime_id(team)
        if runtime_id:
            statements.append(
                select(
                    literal("runtime_event"),
                    cast(RuntimeEvent.id, String),
                    RuntimeEvent.event_type,
                    RuntimeEvent.created_at,
                    RuntimeEvent.message,
                    RuntimeEvent.event_metadata,
                ).where(
                    RuntimeEvent.workspace_id == workspace_id,
                    RuntimeEvent.workspace_runtime_id == runtime_id,
                )
            )
        if filters.include_runs:
            statements.append(
                select(
                    literal("run_event"),
                    cast(RunEvent.id, String),
                    RunEvent.event_type,
                    RunEvent.created_at,
                    RunEvent.message,
                    RunEvent.event_metadata,
                ).where(RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id.in_(run_ids))
            )
        evidence = union_all(*statements).subquery()
        query = select(evidence)
        for column, value in (
            (evidence.c.source_type, filters.source_type),
            (evidence.c.event_type, filters.event_type),
        ):
            if value is not None:
                query = query.where(column == value)
        if filters.since:
            query = query.where(evidence.c.occurred_at >= ensure_aware_utc(filters.since))
        if filters.until:
            query = query.where(evidence.c.occurred_at <= ensure_aware_utc(filters.until))
        filtered = query.subquery()
        source_counts = dict(
            self.session.execute(
                select(filtered.c.source_type, func.count()).group_by(filtered.c.source_type)
            )
            .tuples()
            .all()
        )
        type_counts = dict(
            self.session.execute(
                select(filtered.c.event_type, func.count()).group_by(filtered.c.event_type)
            )
            .tuples()
            .all()
        )
        rows = (
            self.session.execute(
                select(filtered)
                .order_by(
                    filtered.c.occurred_at.desc(), filtered.c.source_type, filtered.c.resource_id
                )
                .limit(filters.limit)
                .offset(filters.offset)
            )
            .mappings()
            .all()
        )
        items = [
            TeamTimelineEvent(
                id=f"{row['source_type']}:{row['resource_id']}",
                source_type=row["source_type"],
                event_type=row["event_type"],
                occurred_at=ensure_aware_utc(row["occurred_at"]),
                resource_id=row["resource_id"],
                message=row["message"],
                metadata=(
                    public_run_event_metadata(row["metadata"] or {})
                    if row["source_type"] == "run_event"
                    else row["metadata"] or {}
                ),
            )
            for row in rows
        ]
        return TeamTimelineResponse(
            workspace_id=workspace_id,
            team_id=team_id,
            generated_at=datetime.now(UTC),
            limit=filters.limit,
            offset=filters.offset,
            summary=TeamTimelineSummary(
                total_events=sum(source_counts.values()),
                returned_events=len(items),
                source_counts=source_counts,
                event_type_counts=type_counts,
                include_runs=filters.include_runs,
            ),
            items=items,
        )
