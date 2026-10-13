"""A durable receipt that native Workflow resource cleanup has completed."""

import asyncio
from collections.abc import Callable

from sqlalchemy.orm import Session
from temporalio import activity

from opsmesh.orchestration.runs.release import release_execution
from opsmesh.runtime.temporal.contracts import ExecutionRequest


class RunExecutionActivities:
    def __init__(self, sessions: Callable[[], Session]) -> None:
        self._sessions = sessions

    @activity.defn(name="opsmesh.execution.release")
    async def release(self, request: ExecutionRequest) -> None:
        def operation() -> None:
            with self._sessions() as session:
                release_execution(session, request.workspace_id, request.resource_id)
                session.commit()

        await asyncio.to_thread(operation)
