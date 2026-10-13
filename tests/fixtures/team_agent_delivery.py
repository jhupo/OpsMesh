"""Drive API-created Team tasks through actual Agent and Team workflows."""

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

from agents import Model
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.execution.providers.openai.activities import AgentExecutionActivities
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.shared.config import Settings
from opsmesh.teams.execution.activities import TeamOrchestrationActivities
from opsmesh.teams.execution.workflow import TeamOrchestrationWorkflow
from tests.fixtures.native_agent_worker import OfflineModelProvider, answer, native_agent_worker


def deliver_team_task(session, task_id: UUID) -> int:
    sessions = sessionmaker(session.get_bind(), expire_on_commit=False)
    settings = Settings(environment="test")
    state = SimpleNamespace(
        sessions=sessions,
        activities=AgentExecutionActivities(sessions, settings),
    )
    responses = []

    class DeliveryModel(Model):
        def __init__(self, reference):
            self.reference = reference

        async def get_response(self, **kwargs):
            with sessions() as read:
                run = read.get(AgentRun, self.reference.run_id)
                assert run is not None and run.task_id == task_id
                step = read.get(TaskStep, run.task_step_id)
                assert step is not None
                review = step.review_policy.get("mode") == "final_acceptance"
                value = (
                    json.dumps(
                        {
                            "decision": "approved",
                            "summary": "Delivery accepted after reviewing specialist results.",
                            "reasons": ["Required work packages are complete"],
                            "revision_requests": [],
                            "missing_work_packages": [],
                        }
                    )
                    if review
                    else f"Completed work package {step.work_package_id}"
                )
            responses.append(self.reference.run_id)
            return answer(value)

        async def stream_response(self, **kwargs):
            raise AssertionError("This acceptance scenario uses the native response Activity")
            yield

    async def execute():
        products = TeamOrchestrationActivities(ActivityDependencies(sessions, settings))
        async with (
            native_agent_worker(state, OfflineModelProvider(DeliveryModel)) as host,
            host.worker(
                additional_workflows=[TeamOrchestrationWorkflow],
                additional_activities=[products.advance, products.record_unknown],
            ),
            asyncio.timeout(90),
        ):
            while True:
                delivered = await host.publisher().publish_pending()
                with sessions() as read:
                    task = read.scalar(select(Task).where(Task.id == task_id))
                    assert task is not None
                    assert task.status not in {"failed", "cancelled"}, task.status
                    if task.status == "completed" and delivered == 0:
                        break
                await asyncio.sleep(0.05)

    asyncio.run(execute())
    return len(responses)
