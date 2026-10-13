"""Durable product approvals and tool idempotency; SDK replay is tested separately."""

import asyncio
import os

import pytest
from sqlalchemy import func, select

from opsmesh.agents.execution.contracts import (
    AgentResumeState,
    AgentRunResult,
    AgentToolInterruption,
)
from opsmesh.agents.execution.providers.openai.activities import AgentExecutionActivities
from opsmesh.agents.execution.providers.openai.tools_temporal import AgentToolOperation
from opsmesh.agents.execution.providers.openai.workflow import (
    AgentApprovalResume,
    AgentExecutionOutcome,
)
from opsmesh.orchestration.approvals.decisions import ApprovalDecisionService
from opsmesh.orchestration.approvals.models import Approval, PendingToolInvocation
from opsmesh.orchestration.runs.models import AgentResumeStateSnapshot, AgentRun
from opsmesh.resources.artifacts.models import Artifact
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.config import Settings

pytest_plugins = ["tests.fixtures.native_agent_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


def test_product_approval_restarts_worker_and_executes_original_tool_once(
    native_execution, tmp_path
):
    state = native_execution
    definition = asyncio.run(state.activities.prepare(state.request))
    assert definition is not None
    arguments = {"filename": "report.txt", "content": "approved evidence", "artifact_type": "text"}
    checkpoint = AgentResumeState(
        provider="openai_agents", serialized_state='{"current_turn":1}', sdk_version="0.22.3"
    )
    paused = AgentRunResult(
        final_output="",
        resume_state=checkpoint,
        interruptions=(
            AgentToolInterruption(
                tool_call_id="original-write",
                tool_name="write_artifact",
                tool_kind="product",
                arguments=arguments,
                policy_decision={"decision": "require_approval", "risk_level": "high"},
            ),
        ),
    )
    asyncio.run(
        state.activities.complete(
            AgentExecutionOutcome(
                request=state.request,
                definition=definition,
                result=paused,
            )
        )
    )
    with state.sessions() as session:
        run = session.get(AgentRun, state.request.resource_id)
        assert run.status == "waiting_approval"
        approval = session.scalar(select(Approval).where(Approval.agent_run_id == run.id))
        assert approval is not None
        approval_id = approval.id
        service = ApprovalDecisionService(session)
        service.approve(approval, state.user_id, "Reviewed")
        service.approve(approval, state.user_id, "Reviewed")
        signals = session.scalars(
            select(TemporalOutboxEntry).where(
                TemporalOutboxEntry.operation == "signal",
                TemporalOutboxEntry.workspace_id == run.workspace_id,
            )
        ).all()
        assert len(signals) == 1
        assert signals[0].workflow_id == state.request.workflow_id
    restarted = AgentExecutionActivities(
        state.sessions, Settings(environment="test", storage_root=str(tmp_path / "artifacts"))
    )
    resumed = asyncio.run(
        restarted.resume(
            AgentApprovalResume(
                request=state.request,
                approval_id=approval_id,
                state=checkpoint,
            )
        )
    )
    assert resumed is not None
    assert resumed.approval_decisions[0].tool_call_id == "original-write"
    operation = AgentToolOperation(
        context=resumed.context,
        tool_name="write_artifact",
        arguments=arguments,
        tool_call_id="original-write",
        approval_granted=True,
    )
    first = asyncio.run(restarted.execute_tool(operation))
    assert first.status == "completed", first
    assert asyncio.run(restarted.execute_tool(operation)) == first
    asyncio.run(
        restarted.complete(
            AgentExecutionOutcome(
                request=state.request,
                definition=resumed,
                result=AgentRunResult(final_output="published"),
            )
        )
    )
    with state.sessions() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Artifact)
                .where(
                    Artifact.workspace_id == state.request.workspace_id,
                    Artifact.task_id == state.task_id,
                )
            )
            == 1
        )
        invocation = session.scalar(
            select(PendingToolInvocation).where(
                PendingToolInvocation.agent_run_id == state.request.resource_id,
            )
        )
        assert invocation.status == "completed" and invocation.attempt_count == 1
        assert invocation.decision_consumed_at is not None
        snapshots = session.scalars(
            select(AgentResumeStateSnapshot).where(
                AgentResumeStateSnapshot.agent_run_id == state.request.resource_id,
            )
        ).all()
        assert snapshots and all(snapshot.status == "consumed" for snapshot in snapshots)
        assert session.get(AgentRun, state.request.resource_id).status == "completed"


@pytest.mark.parametrize("second_decision", ["approved", "rejected"])
def test_approval_arriving_during_resume_remains_available_to_the_next_resume(
    native_execution, second_decision
):
    state = native_execution
    definition = asyncio.run(state.activities.prepare(state.request))
    assert definition is not None
    checkpoint = AgentResumeState(provider="openai_agents", serialized_state='{"turn":1}')
    interruptions = tuple(
        AgentToolInterruption(
            tool_call_id=call_id,
            tool_name="write_artifact",
            tool_kind="product",
            arguments={"filename": f"{call_id}.txt", "content": call_id},
            policy_decision={"decision": "require_approval", "risk_level": "high"},
        )
        for call_id in ("first-call", "second-call")
    )

    def persist(current, pending):
        asyncio.run(
            state.activities.complete(
                AgentExecutionOutcome(
                    request=state.request,
                    definition=current,
                    result=AgentRunResult(
                        final_output="", resume_state=checkpoint, interruptions=pending
                    ),
                )
            )
        )

    persist(definition, interruptions)
    with state.sessions() as session:
        invocations = session.scalars(
            select(PendingToolInvocation).where(
                PendingToolInvocation.agent_run_id == state.request.resource_id
            )
        ).all()
        approvals = {item.tool_call_id: item.approval_id for item in invocations}
        ApprovalDecisionService(session).approve(
            session.get(Approval, approvals["first-call"]), state.user_id, "First approved"
        )
    resumed = asyncio.run(
        state.activities.resume(
            AgentApprovalResume(
                request=state.request, approval_id=approvals["first-call"], state=checkpoint
            )
        )
    )
    assert resumed is not None
    assert [item.tool_call_id for item in resumed.approval_decisions] == ["first-call"]
    # A second HTTP decision commits after the first resume has frozen its input.
    with state.sessions() as session:
        service = ApprovalDecisionService(session)
        decide = service.approve if second_decision == "approved" else service.reject
        decide(session.get(Approval, approvals["second-call"]), state.user_id, "Second reviewed")
    persist(resumed, interruptions[1:])
    following = asyncio.run(
        state.activities.resume(
            AgentApprovalResume(
                request=state.request, approval_id=approvals["second-call"], state=checkpoint
            )
        )
    )
    assert following is not None
    assert [(item.tool_call_id, item.status) for item in following.approval_decisions] == [
        ("second-call", second_decision)
    ]
    with state.sessions() as session:
        invocation = session.scalar(
            select(PendingToolInvocation).where(
                PendingToolInvocation.agent_run_id == state.request.resource_id,
                PendingToolInvocation.tool_call_id == "second-call",
            )
        )
        assert invocation.decision_consumed_at is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(TemporalOutboxEntry)
                .where(
                    TemporalOutboxEntry.workspace_id == state.request.workspace_id,
                    TemporalOutboxEntry.operation == "signal",
                )
            )
            == 2
        )
