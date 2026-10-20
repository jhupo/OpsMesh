"""Durable approval ownership, bounded plans and stale-driver recovery on real PG."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from opsmesh.bootstrap.orchestration import ExecutionApprovalNotifier
from opsmesh.extensions.models import Installation, Invocation
from opsmesh.orchestration.definitions.contracts import (
    FlowCondition,
    FlowDefinitionSpec,
    FlowGraph,
    FlowNode,
    FlowProjection,
    StepCommand,
)
from opsmesh.orchestration.definitions.schemas import DefinitionCreate
from opsmesh.orchestration.definitions.service import create_definition, publish_definition
from opsmesh.orchestration.runs.models import FlowRun, StepAttempt
from opsmesh.orchestration.runs.schemas import (
    FlowControl,
    FlowPlanProposal,
    FlowPlanReview,
    FlowRunCreate,
)
from opsmesh.orchestration.runs.service import accept_plan, control_run, start_run
from opsmesh.platform.approvals.models import ApprovalRequest
from opsmesh.platform.approvals.service import decide
from opsmesh.shared.errors import ConflictError, ForbiddenError
from tests.extensions.test_extensions_postgres import facts as facts
from tests.orchestration.test_flows_postgres import create_flow
from tests.orchestration.test_flows_postgres import flow_facts as flow_facts


def make_run(database_sessions, runtime, actor, graph):
    with database_sessions() as session, session.begin():
        definition = create_definition(
            session,
            actor=actor,
            request=DefinitionCreate(name="Recovery", spec=FlowDefinitionSpec(graph=graph)),
        )
        revision = publish_definition(
            session, actor=actor, definition_id=definition.id, expected_revision=1
        )
        record = start_run(
            session,
            actor=actor,
            request=FlowRunCreate(revision_id=revision.id),
            idempotency_key="recovery",
            cipher=runtime.cipher,
        )
        return record.id


def test_approval_wait_is_owned_and_replays_without_new_request(database_sessions, flow_facts):  # noqa: F811
    runtime, actor, _ = flow_facts
    node = FlowNode(key="review", kind="wait", wait_kind="approval")
    identifier = make_run(database_sessions, runtime, actor, FlowGraph(nodes=[node]))
    command = StepCommand(run_id=identifier, path="review", node=node, arguments={})
    first = runtime.start_step(command)
    assert runtime.start_step(command) == first
    with database_sessions() as session, session.begin():
        approval = session.get(ApprovalRequest, UUID(first["child_id"]))
        assert approval.requester_user_id == actor.user_id
        assert session.scalar(select(func.count()).select_from(ApprovalRequest)) == 1
        decide(
            session,
            approval_id=approval.id,
            decision="approved",
            decided_by=actor,
            reason="Accept fixed occurrence",
            idempotency_key="decision",
            notifier=ExecutionApprovalNotifier(),
        )
    assert runtime.probe(identifier, "review")["status"] == "completed"
    assert runtime.probe(identifier, "review")["result"] == {"decision": "approved"}


def test_retry_retains_occurrences_and_fences_old_projection(database_sessions, flow_facts):  # noqa: F811
    runtime, actor, _ = flow_facts
    identifier = create_flow(database_sessions, flow_facts)
    command = StepCommand(
        run_id=identifier,
        path="signal",
        arguments={},
        node=FlowNode(key="signal", kind="wait", wait_kind="signal", signal_name="answer"),
    )
    first = runtime.start_step(command)
    with database_sessions() as session, session.begin():
        run = session.get(FlowRun, identifier)
        run.status = "needs_attention"
        session.flush()
        control_run(
            session,
            actor=actor,
            run_id=identifier,
            cipher=runtime.cipher,
            request=FlowControl(command_id=uuid4(), expected_revision=run.revision, action="retry"),
        )
        revision = run.revision
    result = runtime.project_state(
        FlowProjection(
            run_id=identifier,
            expected_revision=revision,
            projection_id="old-driver",
            status="failed",
            driver_generation=1,
        )
    )
    assert result["status"] == "queued"
    assert runtime.start_step(command.model_copy(update={"driver_generation": 2})) == first
    with pytest.raises(ConflictError):
        runtime.start_step(command)
    with pytest.raises(ConflictError):
        runtime.probe(identifier, "signal", 1)
    with pytest.raises(ConflictError):
        runtime.complete_local_step(
            {"run_id": str(identifier), "path": "signal", "status": "completed"}
        )
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(StepAttempt)) == 1


def test_dynamic_plan_requires_source_allowlist_and_actual_decision(
    database_sessions,
    facts,  # noqa: F811
    flow_facts,  # noqa: F811
):
    runtime = flow_facts[0]
    user_id, _, _, _, invocation_id = facts
    with database_sessions() as session:
        actor = runtime.users.load_identity(session, user_id)
    graph = FlowGraph(
        nodes=[
            FlowNode(
                key="check",
                kind="condition",
                condition=FlowCondition(operator="eq", left=1, right=1),
            )
        ]
    )
    region = FlowNode(
        key="expand",
        kind="expand",
        allowed_kinds=["condition"],
        max_expanded_nodes=2,
        approval_required=True,
    )
    identifier = make_run(database_sessions, runtime, actor, FlowGraph(nodes=[region]))
    runtime.start_step(StepCommand(run_id=identifier, path="expand", node=region, arguments={}))
    with database_sessions() as session, session.begin():
        source = session.get(Invocation, invocation_id)
        source.snapshot = {
            **source.snapshot,
            "artifact": session.get(Installation, source.installation_id).artifact,
        }
        source.status = "completed"
        source.result_ciphertext = runtime.cipher.encrypt_payload(
            {"value": {"graph": graph.model_dump(mode="json")}}
        ).ciphertext
        revision = session.get(FlowRun, identifier).revision
    proposal = FlowPlanProposal(
        command_id=uuid4(),
        expected_revision=revision,
        step_path="expand",
        source_invocation_id=invocation_id,
        graph=graph,
    )
    with pytest.raises(ForbiddenError), database_sessions() as session, session.begin():
        source = session.get(Invocation, invocation_id)
        session.get(Installation, source.installation_id).artifact = "sha256:" + "f" * 64
        runtime.request_plan_review(
            session, actor=actor, run_id=identifier, request=FlowPlanReview(**proposal.model_dump())
        )
    with pytest.raises(ForbiddenError), database_sessions() as session, session.begin():
        accept_plan(
            session,
            actor=actor,
            run_id=identifier,
            request=proposal,
            cipher=runtime.cipher,
            validate_source=runtime.validate_plan_source,
        )
    with database_sessions() as session, session.begin():
        approval = runtime.request_plan_review(
            session, actor=actor, run_id=identifier, request=FlowPlanReview(**proposal.model_dump())
        )
        approval_id = approval.id
        decide(
            session,
            approval_id=approval_id,
            decision="approved",
            decided_by=actor,
            reason="Reviewed",
            idempotency_key="plan-decision",
            notifier=ExecutionApprovalNotifier(),
        )
    with database_sessions() as session, session.begin():
        accepted = proposal.model_copy(update={"approval_id": approval_id})
        accept_plan(
            session,
            actor=actor,
            run_id=identifier,
            request=accepted,
            cipher=runtime.cipher,
            validate_source=runtime.validate_plan_source,
        )
        session.flush()
        accept_plan(
            session,
            actor=actor,
            run_id=identifier,
            request=accepted,
            cipher=runtime.cipher,
            validate_source=runtime.validate_plan_source,
        )
    assert runtime.probe(identifier, "expand")["graph"] == graph.model_dump(mode="json")
