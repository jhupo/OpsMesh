"""Risk changes are enforced before any new sandbox or tool effect receipt."""

import pytest
from sqlalchemy import func, select

from opsmesh.orchestration.execution.contracts import ExecutionRequest, ToolInvocation
from opsmesh.orchestration.runs.models import OperationReceipt, Run
from opsmesh.orchestration.runs.tool_activities import ToolActivities
from opsmesh.orchestration.runtime.models import Environment, EnvironmentOperationReceipt
from opsmesh.platform.policies.schemas import PolicyWrite
from opsmesh.platform.policies.service import replace_policy
from opsmesh.shared.errors import ForbiddenError
from tests.execution.test_runs_postgres import harness as harness
from tests.execution.test_runtime_postgres import UnusedBackend, environment, runtime, stage


def set_policy(harness, decision):
    with harness.runs.sessions() as session, session.begin():
        replace_policy(
            session,
            key="effects",
            subject=harness.principal,
            payload=PolicyWrite(expected_revision=0, policy={"default": decision}),
        )


def tool_request(harness):
    payload = harness.request.model_dump(mode="json")
    payload["environment_id"] = str(environment(harness))
    payload["agents"]["main"]["tools"] = [
        {
            "name": "write",
            "command": ["python", "write.py"],
            "input_schema": {"type": "object", "properties": {}},
            "approval_required": False,
        }
    ]
    return ExecutionRequest.model_validate(payload)


@pytest.mark.parametrize("decision", ["deny", "require_approval"])
def test_run_claim_rejects_unapproved_policy_before_admission(harness, decision):
    run = harness.submit(request=tool_request(harness))
    set_policy(harness, decision)
    with pytest.raises(ForbiddenError):
        harness.claim(run)
    with harness.runs.sessions() as session:
        assert session.get(Run, run.id).status == "queued"
        assert session.scalar(select(func.count()).select_from(OperationReceipt)) == 0


def test_tool_claim_rechecks_policy_changed_since_run_claim(harness):
    run = harness.submit(request=tool_request(harness))
    assert harness.claim(run) is not None
    set_policy(harness, "deny")
    invocation = ToolInvocation(
        run_id=run.id, agent_key="main", tool_name="write", call_id="call-one", arguments={}
    )
    with pytest.raises(ForbiddenError):
        ToolActivities(harness.runs, UnusedBackend()).claim(invocation)
    with harness.runs.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OperationReceipt)) == 0


@pytest.mark.parametrize("decision", ["deny", "require_approval"])
def test_direct_command_rejection_leaves_pending_receipt_without_effect(harness, decision):
    environment_id = environment(harness)
    command = stage(
        harness, environment_id, "command-one", action="command", command=["python", "-V"]
    )
    set_policy(harness, decision)
    with pytest.raises(ForbiddenError):
        runtime(harness).claim(command)
    with harness.runs.sessions() as session:
        receipt = session.get(
            EnvironmentOperationReceipt, (harness.principal.user_id, command.operation_id)
        )
        assert receipt.status == "pending"
        assert session.get(Environment, environment_id).status == "ready"
