"""Own per-attempt capacity, sandbox resources and generated callback cleanup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from opsmesh.framework.agents import AgentExecutionContext
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import AgentRunView

if TYPE_CHECKING:
    from .ports import ApplicationServices

logger = logging.getLogger(__name__)


class RunResourceScope:
    def __init__(
        self, services: ApplicationServices, run: AgentRunView, context: AgentExecutionContext
    ) -> None:
        self.services, self.run, self.context = services, run, context
        self.owner = f"{run.id}:{run.attempt_id}"
        self.token: str | None = None
        self._heartbeat: asyncio.Task[None] | None = None
        self._native_cleanup: Callable[[], Awaitable[None]] | None = None

    async def open(self, prepare: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
        try:
            await self._acquire()
            executing_task = asyncio.current_task()
            assert executing_task is not None
            self._heartbeat = asyncio.create_task(
                self._renew(executing_task), name=f"admission:{self.run.id}"
            )
            await prepare()
            bindings = await self.services.sandbox_bridge.prepare(self.run, self.context)
            self._native_cleanup = bindings.get("cleanup_resources")
            checkpoint = bindings.get("checkpoint_resources")
            if checkpoint is not None:

                async def checkpoint_owned_resources() -> dict[str, Any]:
                    metadata: dict[str, Any] = await checkpoint()
                    await self.claim_workspace(metadata)
                    return metadata

                bindings["checkpoint_resources"] = checkpoint_owned_resources
            return {
                **bindings,
                "cleanup_resources": self.close,
                "claim_workspace": self.claim_workspace,
            }
        except BaseException:
            await self.close()
            raise

    async def claim_workspace(self, metadata: dict[str, Any]) -> None:
        workspace_id = metadata.get("workspace_id")
        if workspace_id is not None:
            existing = await self.services.workspace_owners.get(workspace_id)
            if existing is None:
                await self.services.workspace_owners.create(
                    workspace_id, {"owner_id": self.run.principal_id}
                )

    async def _acquire(self) -> None:
        while True:
            policy = await self.services.configuration.admission_policy()
            try:
                self.token = await self.services.admission.acquire(
                    self.owner, policy.max_active_runs
                )
                return
            except OpsMeshError as error:
                if error.code != "CAPACITY_BUSY":
                    raise
                await asyncio.sleep(0.1)

    async def _renew(self, executing_task: asyncio.Task[Any]) -> None:
        assert self.token is not None
        try:
            while True:
                await asyncio.sleep(5)
                await self.services.admission.renew(self.owner, self.token)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Backend execution lease was lost", extra={"run_id": self.run.id})
            self.context.cancel_event.set()
            executing_task.cancel()

    async def close(self) -> None:
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat
            self._heartbeat = None
        try:
            if self._native_cleanup is not None:
                await self._native_cleanup()
        finally:
            self.services.memory_tools.release(self.run)
            try:
                await self.services.resources.release(
                    principal_id=self.run.principal_id,
                    run_id=self.run.id,
                    lifecycle_id=self.run.attempt_id,
                )
            finally:
                if self.token is not None:
                    token, self.token = self.token, None
                    with suppress(OpsMeshError):
                        await self.services.admission.release(self.owner, token)
