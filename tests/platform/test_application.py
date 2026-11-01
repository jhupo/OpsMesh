"""Offline end-to-end application behavior through its public orchestration boundary."""

import json
from typing import Any

import pytest

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.dependencies import get_run_context
from opsmesh.framework.agents import (
    AgentBackendCapabilities,
    AgentRunCompleted,
    AgentSpecification,
    ModelUsageObservation,
    ToolBinding,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.capabilities.api import ToolDefinition


class ApplicationTestBackend:
    id = "application_test"
    version = "1"
    features = AgentBackendCapabilities()

    async def run(self, spec, input, context, **options: Any):
        assert get_run_context() is context
        output = await context.invoke_tool(spec.tools[0].tool_ref, {"value": input}, "one")
        await context.services.observe_usage(
            {"call_id": "request-1", "model": "offline", "input_tokens": 4, "output_tokens": 2}
        )
        return AgentRunCompleted(
            output=output,
            usage=ModelUsageObservation(
                requests=1, input_tokens=4, output_tokens=2, total_tokens=6
            ),
            history=({"role": "assistant", "content": output},),
        )


async def configured_application(tmp_path):
    app = OpsMeshApplication(OpsMeshSettings(data_directory=tmp_path, worker_enabled=False))
    app.services.extensions.register_backend("application_test", ApplicationTestBackend)

    async def echo(arguments):
        return f"processed:{arguments['value']}"

    app.services.code_tools.register("tests.echo", echo)
    await app.initialize(admin_username="admin", admin_password="test-passphrase")
    user = (await app.services.accounts.list_users())[0]
    tool = ToolDefinition(
        name="echo",
        version="1",
        code_ref="tests.echo",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    await app.services.capabilities.register_tool(tool)
    draft = await app.services.agents.create(
        AgentSpecification(
            id="helper",
            name="Helper",
            backend="application_test",
            tools=(ToolBinding(name="echo", tool_ref=tool.ref, input_schema=tool.input_schema),),
        ),
        user.id,
    )
    await app.services.agents.publish("helper", draft.revision)
    session = await app.services.sessions.open(user.id, "helper@1")
    return app, user, session


@pytest.mark.asyncio
async def test_application_run_persists_output_costs_history_and_idempotency(tmp_path):
    app, user, session = await configured_application(tmp_path)
    try:
        run = await app.submit_run(
            principal_id=user.id,
            session_id=session.id,
            agent_ref="helper@1",
            input="hello",
            idempotency_key="turn-1",
        )
        assert run.status == "queued"
        completed = await app.services.runtime.execute(run.id)
        assert completed.status == "completed", completed.error
        assert completed.output == "processed:hello"
        assert (await app.services.sessions.get(session.id)).active_run_id is None
        history = await app.services.sessions.history(session.id)
        assert [message.role for message in history] == ["user", "assistant"]
        rollout = await app.services.workspaces.get_blob(completed.metadata["rollout_digest"])
        assert json.loads(rollout)["output"] == "processed:hello"
        costs = await app.services.usage.costs(user.id, run.id)
        assert len(costs) == 1 and costs[0].status == "unpriced"
        replay = await app.submit_run(
            principal_id=user.id,
            session_id=session.id,
            agent_ref="helper@1",
            input="hello",
            idempotency_key="turn-1",
        )
        assert replay.status == "completed" and replay.id == run.id
        assert len(await app.services.sessions.history(session.id)) == 2
        with pytest.raises(OpsMeshError, match="different input"):
            await app.submit_run(
                principal_id=user.id,
                session_id=session.id,
                agent_ref="helper@1",
                input="different",
                idempotency_key="turn-1",
            )
        facts = await app.services.database.pending_events()
        for fact in facts:
            await app.outbox.deliver(fact)
        count = len(await app.services.audit.query())
        for fact in facts:
            await app.outbox.deliver(fact)
        assert len(await app.services.audit.query()) == count
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_owner_checks_and_revocation_precede_backend_execution(tmp_path):
    app, user, session = await configured_application(tmp_path)
    try:
        other = await app.services.accounts.create_user("other", "test-passphrase")
        with pytest.raises(OpsMeshError, match="not permitted"):
            await app.submit_run(
                principal_id=other.id,
                session_id=session.id,
                agent_ref="helper@1",
                input="hello",
                idempotency_key="foreign",
            )
        run = await app.submit_run(
            principal_id=user.id,
            session_id=session.id,
            agent_ref="helper@1",
            input="hello",
            idempotency_key="revoke",
        )
        await app.services.accounts.update_user(user.id, active=False)
        failed = await app.services.runtime.execute(run.id)
        assert failed.status == "blocked" and failed.error.code == "FORBIDDEN"
        assert not await app.services.usage.costs(user.id, run.id)
    finally:
        await app.close()


@pytest.mark.asyncio
async def test_workspace_restore_authorizes_actual_snapshot_owner(tmp_path):
    app, user, _ = await configured_application(tmp_path)
    try:
        await app.create_workspace(user.id, "workspace")
        await app.services.workspaces.write("workspace", "report.txt", b"report")
        snapshot = await app.services.workspaces.snapshot("workspace")
        other = await app.services.accounts.create_user("other", "test-passphrase")
        with pytest.raises(OpsMeshError, match="not permitted"):
            await app.restore_workspace(
                principal_id=other.id, snapshot_id=snapshot.id, target_workspace_id="stolen"
            )
        restored = await app.restore_workspace(
            principal_id=user.id, snapshot_id=snapshot.id, target_workspace_id="restored"
        )
        assert restored.id == "restored"
        assert await app.services.workspaces.read("restored", "report.txt") == b"report"
    finally:
        await app.close()
