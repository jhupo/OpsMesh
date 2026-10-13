"""Execute an admitted Team product request with a real native Temporal Worker."""

import asyncio
from uuid import UUID

from sqlalchemy.orm import sessionmaker
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.converter import sdk_data_converter
from opsmesh.shared.config import Settings
from opsmesh.teams.execution.activities import TeamOrchestrationActivities
from opsmesh.teams.execution.models import TeamOrchestrationRequest
from opsmesh.teams.execution.workflow import TeamOrchestrationWorkflow


def run_team_request(session, request_id: UUID):
    sessions = sessionmaker(session.get_bind(), expire_on_commit=False)
    with sessions() as read_session:
        command = read_session.get(TeamOrchestrationRequest, request_id)
        assert command is not None
        request = ExecutionRequest(
            execution_id=command.id,
            workspace_id=command.workspace_id,
            resource_id=command.id,
            kind=ExecutionKind.TEAM_ORCHESTRATION,
            requested_by_user_id=UUID(str(command.execution_identity["user_id"])),
        )

    async def execute():
        environment = await WorkflowEnvironment.start_local(data_converter=sdk_data_converter)
        try:
            async with Worker(
                environment.client,
                task_queue="team-api-validation",
                workflows=[TeamOrchestrationWorkflow],
                activities=[
                    TeamOrchestrationActivities(
                        ActivityDependencies(sessions, Settings(environment="test"))
                    ).advance,
                    TeamOrchestrationActivities(
                        ActivityDependencies(sessions, Settings(environment="test"))
                    ).record_unknown,
                ],
            ):
                await environment.client.execute_workflow(
                    TeamOrchestrationWorkflow.run,
                    request,
                    id=request.workflow_id,
                    task_queue="team-api-validation",
                )
        finally:
            await environment.shutdown()

    asyncio.run(execute())
    session.expire_all()
    command = session.get(TeamOrchestrationRequest, request_id)
    assert command.status == "completed", command.last_error_code
    return command.result
