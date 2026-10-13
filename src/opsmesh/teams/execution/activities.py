"""Reauthorize and serialize Team product transitions at the Activity boundary."""

import asyncio
from datetime import UTC, datetime

from pydantic import TypeAdapter
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resource_queries import execution_resource_queries
from opsmesh.runtime.instances.service import RuntimeEnvironmentService
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionRequest
from opsmesh.teams.execution.dispatch import require_team_transition_authority
from opsmesh.teams.execution.models import TeamOrchestrationRequest
from opsmesh.teams.execution.reconciliation import require_reconciled_team
from opsmesh.teams.execution.service import TeamOrchestrationService
from opsmesh.teams.sessions.schemas import AgentTeamOrchestrationAdvanceRequest


class TeamOrchestrationActivities:
    def __init__(self, services: ActivityDependencies) -> None:
        self._services = services

    @activity.defn(name="opsmesh.team.advance")
    async def advance(self, request: ExecutionRequest) -> None:
        pending = asyncio.create_task(asyncio.to_thread(self._advance, request))
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            # A database transition cannot be interrupted mid-commit. Wait for
            # its advisory lock and effects to settle before cancellation ACK.
            try:
                await pending
            finally:
                raise

    @activity.defn(name="opsmesh.team.record_unknown")
    async def record_unknown(self, request: ExecutionRequest) -> None:
        def record() -> None:
            with self._services.sessions() as session:
                command = session.scalar(
                    select(TeamOrchestrationRequest)
                    .where(
                        TeamOrchestrationRequest.workspace_id == request.workspace_id,
                        TeamOrchestrationRequest.id == request.resource_id,
                    )
                    .with_for_update()
                )
                if command is not None and command.status == "running":
                    command.status = "unknown"
                    command.last_error_code = "ActivityOutcomeUnknown"
                    session.commit()
                elif command is not None and command.status == "queued":
                    command.status = "failed"
                    command.last_error_code = "ActivityDidNotStart"
                    command.completed_at = datetime.now(UTC)
                    session.commit()

        await asyncio.to_thread(record)

    def _advance(self, request: ExecutionRequest) -> None:
        with self._services.sessions() as lookup:
            command = lookup.scalar(
                select(TeamOrchestrationRequest).where(
                    TeamOrchestrationRequest.workspace_id == request.workspace_id,
                    TeamOrchestrationRequest.id == request.resource_id,
                )
            )
            if command is None:
                raise ApplicationError("Team request not found", non_retryable=True)
            team_id = command.team_id
            bind = lookup.get_bind()
        if not isinstance(bind, Engine) or bind.dialect.name != "postgresql":
            raise ValueError("Team transition serialization requires PostgreSQL")
        # The dedicated connection retains this product lock across service commits.
        # It never stays checked out during SDK or model execution.
        with bind.connect() as connection:
            lock_key = f"{request.workspace_id}:team:{team_id}"
            connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), {"key": lock_key}
            )
            connection.commit()
            try:
                with Session(bind=connection, expire_on_commit=False) as session:
                    self._execute(session, request)
            finally:
                connection.rollback()
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": lock_key}
                )
                connection.commit()

    def _execute(self, session: Session, request: ExecutionRequest) -> None:
        command = session.scalar(
            select(TeamOrchestrationRequest)
            .where(
                TeamOrchestrationRequest.workspace_id == request.workspace_id,
                TeamOrchestrationRequest.id == request.resource_id,
            )
            .with_for_update()
        )
        if command is None:
            raise ApplicationError("Team request not found", non_retryable=True)
        if command.status == "completed":
            return
        if command.status != "queued":
            raise ApplicationError("Team request requires reconciliation", non_retryable=True)
        try:
            require_reconciled_team(session, request.workspace_id, command.team_id)
            user = ExecutionIdentityService(session).restore(
                request.workspace_id, command.execution_identity
            )
            if user.user_id != request.requested_by_user_id:
                raise ValueError("Team request actor differs")
            configuration = AgentTeamOrchestrationAdvanceRequest.model_validate(
                command.configuration
            )
            require_team_transition_authority(
                session,
                workspace_id=request.workspace_id,
                team_id=command.team_id,
                task_id=command.task_id,
                user=user,
                configuration=configuration,
            )
        except Exception as exc:
            session.rollback()
            command.status = "failed"
            command.completed_at = datetime.now(UTC)
            command.last_error_code = type(exc).__name__[:100]
            session.commit()
            raise ApplicationError(
                "Team request failed authorization or configuration validation",
                type=command.last_error_code,
                non_retryable=True,
            ) from None
        command.status = "running"
        command.started_at = datetime.now(UTC)
        session.commit()
        try:
            with execution_resource_queries(session, request.workspace_id, user):
                service = TeamOrchestrationService(session)
                result: dict[str, object] | None
                if command.task_id is not None:
                    result = service.advance_task(
                        workspace_id=request.workspace_id,
                        team_id=command.team_id,
                        task_id=command.task_id,
                        actor_user_id=user.user_id,
                    )
                else:
                    result = service.advance(
                        workspace_id=request.workspace_id,
                        team_id=command.team_id,
                        actor_user_id=user.user_id,
                        **configuration.model_dump(exclude={"dry_run"}),
                        dry_run=False,
                        runtime_control=RuntimeEnvironmentService(session, self._services.settings),
                    )
            if result is None:
                raise ValueError("Team is unavailable")
            normalized = TypeAdapter(dict[str, object]).dump_python(result, mode="json")
            if not isinstance(normalized, dict):
                raise ValueError("Team result must be an object")
            command.result = normalized
            command.status = "completed"
            command.completed_at = datetime.now(UTC)
            session.commit()
        except BaseException as exc:
            session.rollback()
            command.status = "unknown"
            command.last_error_code = type(exc).__name__[:100]
            session.commit()
            raise
