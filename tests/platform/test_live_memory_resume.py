"""Live memory survives SDK approval resume while old attempt ownership is released."""

from datetime import UTC, datetime

import pytest

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.framework.agents import AgentExecutionContext, AgentSpecification, ToolBinding
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.memory.api import MemoryCandidate, MemoryNamespace, MemoryPolicy
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.runtime.internal.backends.openai_agents.backend import OpenAIAgentsBackend
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from tests.runtime.test_openai_backend import ScriptedModel, call, message


async def test_live_memory_approval_resumes_on_new_attempt_and_reads_current_memory(tmp_path):
    app = OpsMeshApplication(OpsMeshSettings(data_directory=tmp_path, worker_enabled=False))
    await app.initialize(admin_username="admin", admin_password="local-test-password")
    try:
        principal = (await app.services.accounts.list_users())[0]
        await app.services.memory.create_namespace(
            MemoryNamespace(id="memory", owner=principal.id, policy=MemoryPolicy(live_update=True))
        )
        spec = AgentSpecification(
            id="a",
            name="Approval with live memory",
            memory_namespace_id="memory",
            tools=(ToolBinding(name="gate", requires_approval=True),),
        )
        now = datetime.now(UTC)
        run = AgentRunView(
            id="run",
            revision=1,
            principal_id=principal.id,
            session_id="session",
            agent=spec,
            status="running",
            attempt_id="first",
            created_at=now,
            updated_at=now,
        )
        observed = []

        async def invoke(reference, arguments, call_id):
            if reference == "gate":
                return "approved"
            result = await app.services.tool_invoker.invoke(
                reference, arguments, principal_id=principal.id, call_id=call_id
            )
            observed.append(result)
            return result

        async def resolve(reference):
            raise AssertionError("No delegated agent should be resolved")

        def execution(attempt):
            context = AgentExecutionContext(run.id, run.session_id, principal.id, invoke, resolve)

            async def prepare(_specification):
                return await app.services.memory_tools.prepare(attempt, "memory")

            context.services.prepare_agent_resources = prepare
            return context

        model = ScriptedModel(
            [[call("gate")], [call("read_memory", call_id="memory-read")], [message("done")]]
        )
        backend = OpenAIAgentsBackend(RuntimeExtensionRegistry(), model_resolver=lambda *_: model)
        pending = await backend.run(spec, "Read memory after approval", execution(run))
        assert pending.pending[0].tool_name == "gate"
        await app.services.memory.update(
            "memory",
            MemoryCandidate(summary="new factual memory", index="latest index"),
            principal_id=principal.id,
            expected_revision=1,
            idempotency_key="between-attempts",
        )
        resumed = run.model_copy(update={"attempt_id": "second"})
        prepared = await app.services.memory_tools.prepare(resumed, "memory")
        app.services.memory_tools.release(run)
        result = await backend.run(
            spec,
            "ignored on resume",
            execution(resumed),
            checkpoint=pending.checkpoint,
            decisions={pending.pending[0].id: True},
        )
        assert result.output == "done"
        assert observed[0]["summary"] == "new factual memory"
        assert observed[0]["memory_revision"] == 1
        definition = await app.services.capabilities.get_tool(prepared.tools[1].tool_ref)
        callback = app.services.code_tools.resolve(definition.code_ref)
        app.services.memory_tools.release(resumed)
        with pytest.raises(OpsMeshError, match="released"):
            await callback({})
    finally:
        await app.close()
