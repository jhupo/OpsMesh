"""MCP admission and unknown effects against PG; transport I/O is tested separately."""


import asyncio
from uuid import uuid4

import pytest

from opsmesh.orchestration.execution.contracts import MCPBinding
from opsmesh.orchestration.runs import reconciliation
from opsmesh.orchestration.runs.contracts import RunCompletion
from opsmesh.orchestration.runs.mcp import RunMCPMeter
from opsmesh.orchestration.runs.models import Run
from opsmesh.orchestration.runs.schemas import ReconcileRequest
from opsmesh.orchestration.runtime import mcp_service
from opsmesh.orchestration.runtime.contracts import MCPCommand
from opsmesh.orchestration.runtime.mcp import MCPServerFactory
from opsmesh.orchestration.runtime.mcp_activities import MCPActivities
from opsmesh.orchestration.runtime.models import Environment, MCPConnection, MCPOperationReceipt
from opsmesh.shared.errors import DomainError
from tests.execution.test_runs_postgres import harness  # noqa: F401
from tests.execution.test_runtime_postgres import environment


def test_stdio_effect_owns_environment_until_confirmed(harness):  # noqa: F811
    env = environment(harness)
    with harness.runs.sessions() as session, session.begin():
        connection = MCPConnection(
            scope_id=harness.principal.user_id,
            principal_id=harness.principal.user_id,
            owner_ref=f"user:{harness.principal.user_id}",
            name="stdio-test",
            transport="stdio",
            environment_id=env,
            status="ready",
            configuration={"command": ["server"]},
            tools=[],
        )
        session.add(connection)
        session.flush()
    agent = harness.request.agents["main"].model_copy(
        update={
            "mcp_connections": [
                MCPBinding(
                    connection_id=connection.id, tools_digest="a" * 64, allowed_tools=["echo"]
                )
            ]
        }
    )
    request = harness.request.model_copy(update={"agents": {"main": agent}})
    run = harness.submit(request=request)
    harness.claim(run)
    context = {
        "run_id": str(run.id),
        "principal_id": str(harness.principal.user_id),
        "agent_key": "main",
        "connection_id": str(connection.id),
        "tools_digest": "a" * 64,
    }
    meter = RunMCPMeter(harness.runs)
    lifecycle = MCPActivities(
        MCPServerFactory(
            harness.runs.sessions, harness.runs.admission, harness.runs.cipher, None, meter
        )
    )

    def close(operation):
        with harness.runs.sessions() as session, session.begin():
            item = session.get(MCPConnection, connection.id)
            mcp_service.stage(session, item=item, operation_id=operation, action="close")
        command = MCPCommand(
            execution_id=uuid4(),
            workflow_id=f"mcp-close:{operation}",
            connection_id=connection.id,
            principal_id=harness.principal.user_id,
            operation_id=operation,
            action="close",
        )
        return asyncio.run(lifecycle.operate(command))

    assert meter._claim(context, "echo", {}, "stdio-effect")[0]
    with pytest.raises(DomainError):
        close("close-started")
    with harness.runs.sessions() as session:
        assert session.get(Environment, env).active_operation_id == "stdio-effect"
        assert session.get(MCPConnection, connection.id).status == "ready"
        receipt = session.get(MCPOperationReceipt, (connection.scope_id, "close-started"))
        assert receipt.status == "completed" and receipt.result["not_executed"]
    meter._complete(run.id, "stdio-effect", None)
    with pytest.raises(DomainError):
        close("close-uncertain")
    with harness.runs.sessions() as session:
        assert session.get(Environment, env).status == "needs_attention"
    with pytest.raises(DomainError):
        meter._claim(context, "echo", {}, "stdio-effect")
    harness.runs._finish(RunCompletion(run_id=run.id, status="failed"))
    with harness.runs.sessions() as session, session.begin():
        current = session.get(Run, run.id)
        reconciliation.reconcile(
            session,
            run_id=run.id,
            actor=harness.principal,
            request=ReconcileRequest(
                command_id=uuid4(),
                expected_revision=current.event_sequence,
                evidence={"operator_attestation": "the tool was not executed"},
                operations=[{"operation_id": "stdio-effect", "outcome": "not_executed"}],
            ),
            cipher=harness.runs.cipher,
            bindings=harness.runs.bindings,
        )
    with harness.runs.sessions() as session:
        recovered = session.get(Environment, env)
        assert recovered.status == "ready" and recovered.active_operation_id is None
    assert close("close-reconciled").status == "completed"
    with harness.runs.sessions() as session:
        assert session.get(MCPConnection, connection.id).status == "closed"
