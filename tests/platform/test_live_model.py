"""Explicit paid-provider acceptance; never enabled by the default test suite."""

import json
import os

import pytest

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.framework.agents import AgentSpecification, ToolBinding
from opsmesh.modules.capabilities.api import ToolDefinition
from opsmesh.modules.models.api import ModelProfile

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPSMESH_TEST_LIVE_MODEL") != "1",
        reason="Explicit live model opt-in and credentials are required",
    ),
]


async def create_application(path, calls):
    app = OpsMeshApplication(OpsMeshSettings(data_directory=path, worker_enabled=False))

    async def confirmed_value(arguments):
        calls.append(arguments)
        return "OPSMESH_APPROVED_42"

    app.services.code_tools.register("acceptance.confirmed_value@1", confirmed_value)
    return app


async def configure_profile(app):
    await app.initialize(admin_username="admin", admin_password="acceptance-only-password")
    principal = (await app.services.accounts.list_users())[0]
    await app.services.models.create(
        ModelProfile(
            id="acceptance",
            model=os.environ["OPSMESH_TEST_MODEL"],
            base_url=os.environ["OPSMESH_TEST_MODEL_BASE_URL"],
            secret_ref="env:OPSMESH_TEST_MODEL_KEY",
            settings={"max_tokens": 512, "store": False, "retry": {"max_retries": 0}},
        )
    )
    return principal


async def publish(app, principal, spec):
    draft = await app.services.agents.create(spec, principal.id)
    await app.services.agents.publish(spec.id, draft.revision)
    return await app.services.sessions.open(principal.id, spec.ref)


async def test_live_approval_survives_application_restart_without_duplicate_tool(tmp_path):
    calls = []
    app = await create_application(tmp_path, calls)
    try:
        principal = await configure_profile(app)
        tool = ToolDefinition(
            name="confirmed_value",
            version="1",
            code_ref="acceptance.confirmed_value@1",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        )
        await app.services.capabilities.register_tool(tool)
        spec = AgentSpecification(
            id="approval",
            name="Approval acceptance",
            model_profile_ref="acceptance@1",
            instructions="Call confirmed_value exactly once. Do not invent a tool result.",
            tools=(
                ToolBinding(
                    name=tool.name,
                    tool_ref=tool.ref,
                    input_schema=tool.input_schema,
                    requires_approval=True,
                ),
            ),
            tool_choice=tool.name,
            tool_completion="stop_on_first_tool",
            max_turns=2,
        )
        session = await publish(app, principal, spec)
        await app.start()
        request = dict(
            principal_id=principal.id,
            session_id=session.id,
            agent_ref=spec.ref,
            input="Call the tool now.",
            idempotency_key="approval-round-trip",
        )
        queued = await app.submit_run(**request)
        waiting = await app.services.runtime.execute(queued.id)
        assert waiting.status == "waiting_approval", waiting.error
        assert waiting.has_checkpoint and len(waiting.approvals) == 1 and calls == []
        await app.close()
        app = await create_application(tmp_path, calls)
        await app.start()
        restored = await app.services.runtime.get_run(queued.id)
        decided = await app.services.runtime.decide(
            restored.id, restored.approvals[0].id, True, restored.revision
        )
        await app.services.runtime.resume(restored.id, decided.revision)
        completed = await app.services.runtime.execute(restored.id)
        assert completed.status == "completed", completed.error
        assert completed.output == "OPSMESH_APPROVED_42" and calls == [{}]
        assert completed.usage.requests == 1 and completed.usage.total_tokens > 0
        costs = await app.services.usage.costs(principal.id, completed.id)
        assert len(costs) == 1 and costs[0].status == "unpriced"
        replay = await app.submit_run(**request)
        assert replay.id == completed.id and replay.status == "completed"
        assert calls == [{}]
        history = await app.services.sessions.history(session.id)
        assert [row.role for row in history] == ["user", "assistant"]
    finally:
        await app.close()


async def test_live_streaming_structured_output_persists_history_and_metering(tmp_path):
    app = await create_application(tmp_path, [])
    try:
        principal = await configure_profile(app)
        spec = AgentSpecification(
            id="structured",
            name="Structured streaming acceptance",
            model_profile_ref="acceptance@1",
            instructions="Return status=OPSMESH_OK and value=42 in the requested JSON schema.",
            output_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "value": {"type": "integer"},
                },
                "required": ["status", "value"],
                "additionalProperties": False,
            },
            max_turns=2,
        )
        session = await publish(app, principal, spec)
        await app.start()
        queued = await app.submit_run(
            principal_id=principal.id,
            session_id=session.id,
            agent_ref=spec.ref,
            input="Return the verification object.",
            idempotency_key="structured-stream",
        )
        completed = await app.services.runtime.execute(queued.id)
        assert completed.status == "completed", completed.error
        assert completed.output == {"status": "OPSMESH_OK", "value": 42}
        events = [event async for event in app.services.runtime.subscribe(completed.id)]
        assert any(event.kind == "text_delta" for event in events)
        assert events[-1].kind == "completed"
        assert completed.usage.requests == 1 and completed.usage.total_tokens > 0
        costs = await app.services.usage.costs(principal.id, completed.id)
        assert len(costs) == 1 and costs[0].status == "unpriced"
        rollout = await app.services.workspaces.get_blob(completed.metadata["rollout_digest"])
        assert json.loads(rollout)["output"] == completed.output
        assert len(await app.services.sessions.history(session.id)) == 2
    finally:
        await app.close()
