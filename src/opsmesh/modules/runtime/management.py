"""Public Run operations; worker execution and recovery internals stay infrastructure-owned."""

from collections.abc import AsyncIterator
from typing import Any, Protocol

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.audit.operations import audited_operation

from .api import AgentRunView, RunOverrides, RunStatus, RunStreamEvent
from .approval_management import ApprovalManagement


class RunSubmission(Protocol):
    async def __call__(
        self,
        *,
        principal_id: str,
        session_id: str,
        agent_ref: str,
        input: str | list[dict[str, Any]],
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
        overrides: RunOverrides | None = None,
    ) -> AgentRunView: ...


class RunManagement(ApprovalManagement):
    submit_run: RunSubmission | None = None

    async def submit(
        self,
        *,
        actor_id: str,
        session_id: str,
        agent_ref: str,
        input: str | list[dict[str, Any]],
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
        overrides: RunOverrides | None = None,
    ) -> AgentRunView:
        if self.submit_run is None:
            raise OpsMeshError("CONFIG_INVALID", "Run submission is not configured")
        async with audited_operation(self.audit, "run.submit", session_id, actor_id):
            return await self.submit_run(
                principal_id=actor_id,
                session_id=session_id,
                agent_ref=agent_ref,
                input=input,
                idempotency_key=idempotency_key,
                metadata=metadata,
                overrides=overrides,
            )

    async def get_run(self, run_id: str, *, actor_id: str) -> AgentRunView:
        run = await self.runtime.get_run(run_id)
        await self.authorize(actor_id, run, "run:read")
        return run

    async def find_submission(self, idempotency_key: str, *, actor_id: str) -> AgentRunView | None:
        """Resolve this actor's submission without scanning unrelated session history."""
        actor = await self.accounts.get_principal(actor_id)
        if not actor.active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")
        run = await self.runtime.find_submission(actor_id, idempotency_key)
        if run is not None:
            await self.authorize(actor_id, run, "run:read")
        return run

    async def list_runs(
        self,
        *,
        actor_id: str,
        principal_id: str | None = None,
        session_id: str | None = None,
        status: RunStatus | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[AgentRunView]:
        actor = await self.accounts.get_principal(actor_id)
        if not actor.active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")
        result = []
        for run in await self.runtime.list_runs(
            principal_id,
            session_id=session_id,
            statuses=(status,) if status else (),
            limit=limit,
            offset=offset,
        ):
            try:
                await self.authorize(actor_id, run, "run:read")
            except OpsMeshError as error:
                if error.code != "FORBIDDEN":
                    raise
            else:
                result.append(run)
        return result

    async def cancel(self, run_id: str, *, actor_id: str) -> AgentRunView:
        await self.authorize(actor_id, await self.runtime.get_run(run_id), "run:cancel")
        async with audited_operation(self.audit, "run.cancel", run_id, actor_id):
            return await self.runtime.cancel(run_id)

    async def resume(self, run_id: str, expected_revision: int, *, actor_id: str) -> AgentRunView:
        await self.authorize(actor_id, await self.runtime.get_run(run_id), "run:submit")
        async with audited_operation(
            self.audit,
            "run.resume",
            run_id,
            actor_id,
            details={"expected_revision": expected_revision},
        ):
            return await self.runtime.resume(run_id, expected_revision)

    async def recover(self, run_id: str, expected_revision: int, *, actor_id: str) -> AgentRunView:
        await self.authorize(actor_id, await self.runtime.get_run(run_id), "run:recover")
        async with audited_operation(
            self.audit,
            "run.recover",
            run_id,
            actor_id,
            details={"expected_revision": expected_revision},
        ):
            return await self.runtime.recover(run_id, expected_revision)

    async def subscribe(
        self, run_id: str, after_seq: int = 0, *, actor_id: str
    ) -> AsyncIterator[RunStreamEvent]:
        await self.get_run(run_id, actor_id=actor_id)
        async for event in self.runtime.subscribe(run_id, after_seq):
            await self.get_run(run_id, actor_id=actor_id)
            yield event
