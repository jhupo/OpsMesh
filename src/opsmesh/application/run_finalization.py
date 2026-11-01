"""Durable result finalization: sandbox artifacts, display history, usage and learned memory."""

import json
from typing import Any

from opsmesh.framework.agents import AgentRunCompleted
from opsmesh.infrastructure.database import VersionedRecordStore
from opsmesh.modules.memory.api import MemoryService, MemorySource
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.sessions.api import Conversations
from opsmesh.modules.workspaces.api import WorkspaceService

from .run_usage import RunUsageAccounting
from .sandbox_bridge import SandboxWorkspaceBridge


class RunResultFinalizer:
    def __init__(
        self,
        *,
        sandbox: SandboxWorkspaceBridge,
        sessions: Conversations,
        workspaces: WorkspaceService,
        workspace_owners: VersionedRecordStore,
        memory: MemoryService,
        usage: RunUsageAccounting,
    ) -> None:
        self.sandbox_bridge, self.sessions, self.workspaces = sandbox, sessions, workspaces
        self.workspace_owners, self.memory, self.usage = workspace_owners, memory, usage

    async def finalize(self, run: AgentRunView, outcome: AgentRunCompleted) -> dict[str, Any]:
        sandbox = await self.sandbox_bridge.finalize(run, outcome)
        if workspace_id := sandbox.get("workspace_id"):
            existing = await self.workspace_owners.get(workspace_id)
            if existing is None:
                await self.workspace_owners.create(workspace_id, {"owner_id": run.principal_id})
        output = (
            outcome.output
            if isinstance(outcome.output, str)
            else json.dumps(outcome.output, ensure_ascii=False)
        )
        await self.sessions.append(
            run.session_id, "assistant", output, message_id=f"{run.id}:assistant", run_id=run.id
        )
        rollout = json.dumps(
            {"run_id": run.id, "output": outcome.output, "history": outcome.history},
            ensure_ascii=False,
        ).encode()
        digest = await self.workspaces.put_blob(rollout)
        await self.usage.finish(run, outcome)
        if run.agent.memory_namespace_id and run.agent.memory_generate:
            await self.memory.append_source(
                MemorySource(
                    id=run.id,
                    namespace_id=run.agent.memory_namespace_id,
                    conversation_id=run.session_id,
                    run_id=run.id,
                    text=rollout.decode(),
                ),
                principal_id=run.principal_id,
            )
            await self.memory.request_generation(
                run.agent.memory_namespace_id, principal_id=run.principal_id, idempotency_key=run.id
            )
        return {**sandbox, "rollout_digest": digest}
