"""Product authorization and atomic persistence for orchestration nodes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session
from temporalio import activity
from temporalio.exceptions import ApplicationError

from opsmesh.agents.execution.contracts import (
    AgentRunResult,
    AgentStructuredOutput,
    ToolExecutionResult,
)
from opsmesh.agents.execution.errors import AgentModelProviderError
from opsmesh.agents.execution.tools.authorization import AgentToolAuthorizer
from opsmesh.agents.execution.tools.executor import AgentToolExecutionService
from opsmesh.capabilities.references.schema import validate_json_value
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.approvals.pending_tools import (
    PendingToolInvocationRequest,
    PendingToolInvocationService,
)
from opsmesh.orchestration.approvals.service import ApprovalService
from opsmesh.orchestration.definitions.data import resolve_workflow_inputs
from opsmesh.orchestration.definitions.snapshot import node_definition_snapshot
from opsmesh.orchestration.definitions.subworkflows import SubworkflowExecutionService
from opsmesh.orchestration.definitions.workflow import NodeExecutionDefinition, NodeExecutionOutcome
from opsmesh.orchestration.requests.builder import RunRequestBuilder
from opsmesh.orchestration.runs.authorization.validation import authorized_profile_for_run
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.runs.queries import authorization_snapshot_for_run
from opsmesh.orchestration.runs.service import RunOrchestrationService
from opsmesh.orchestration.runs.tree_lock import lock_run_execution_tree
from opsmesh.orchestration.tasks.models import Task, TaskStep
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.utils import payload_hash

TERMINAL_NODE_STATUSES = frozenset({"completed", "failed", "cancelled"})


@dataclass(frozen=True)
class OrchestrationNodeActivities:
    services: ActivityDependencies

    def _run(self, session: Session, request: ExecutionRequest) -> AgentRun:
        if request.kind != ExecutionKind.ORCHESTRATION:
            raise ValueError("Node Activity received an invalid execution kind")
        lock_run_execution_tree(session, request.workspace_id, request.resource_id)
        run = session.scalar(
            select(AgentRun)
            .where(
                AgentRun.workspace_id == request.workspace_id,
                AgentRun.id == request.resource_id,
            )
            .with_for_update()
        )
        if run is None:
            raise ApplicationError("Run is unavailable", non_retryable=True)
        return run

    @activity.defn(name="opsmesh.orchestration.prepare")
    def prepare(self, request: ExecutionRequest) -> NodeExecutionDefinition | None:
        with self.services.transaction() as session:
            run = self._run(session, request)
            if run.status in TERMINAL_NODE_STATUSES:
                return None
            identity = ExecutionIdentityService(session).for_run(run.workspace_id, run.id)
            step = session.scalar(
                select(TaskStep).where(
                    TaskStep.workspace_id == run.workspace_id,
                    TaskStep.id == run.task_step_id,
                    TaskStep.task_id == run.task_id,
                )
            )
            task = session.scalar(
                select(Task).where(
                    Task.workspace_id == run.workspace_id,
                    Task.id == run.task_id,
                )
            )
            if step is None or task is None or not isinstance(step.dependencies, dict):
                raise ApplicationError("Node is unavailable", non_retryable=True)
            lifecycle = RunOrchestrationService(session)._run_lifecycle()
            if task.status == "cancelled":
                lifecycle.mark_run_cancelled(run, completed_at=datetime.now(UTC))
                return None
            builder = RunRequestBuilder(session, self.services.settings)
            # This validates the frozen definition and current resource permissions
            # without selecting a model or building a second conversation history.
            builder.validate_job_scope(run, request)
            builder.validate_authorization_snapshot(
                run,
                task,
                authorized_profile_for_run(session, run),
                authorization_snapshot_for_run(run),
            )
            frozen = authorization_snapshot_for_run(run).get("node_definition")
            if not isinstance(frozen, dict) or frozen != node_definition_snapshot(step):
                raise ApplicationError(
                    "Workflow node configuration changed after admission", non_retryable=True
                )
            if run.status in {"queued", "waiting_approval"}:
                lifecycle.mark_run_started(run)
            node_type = step.dependencies.get("node_type")
            if node_type == "approval":
                approval = session.scalar(
                    select(Approval).where(
                        Approval.workspace_id == run.workspace_id,
                        Approval.agent_run_id == run.id,
                        Approval.approval_type == "workflow.node",
                    )
                )
                if approval is None:
                    approval = ApprovalService(session).create_approval(
                        workspace_id=run.workspace_id,
                        task_id=run.task_id,
                        agent_run_id=run.id,
                        requested_by_agent_profile_id=run.agent_profile_id,
                        approval_type="workflow.node",
                        risk_level="medium",
                        payload={"node_type": "approval", "title": step.title},
                    )
                if approval.status == "pending":
                    lifecycle.mark_run_waiting_approval(run)
                    return NodeExecutionDefinition(
                        node_type=node_type,
                        status="waiting_approval",
                        approval_id=approval.id,
                    )
                return NodeExecutionDefinition(
                    node_type=node_type,
                    status="ready",
                    result=ToolExecutionResult(
                        status="completed" if approval.status == "approved" else "failed",
                        output={"approval_id": str(approval.id), "decision": approval.status},
                        error={"code": "approval_rejected", "message": "Node approval rejected"}
                        if approval.status != "approved"
                        else None,
                    ),
                )
            if node_type == "subworkflow":
                launch = SubworkflowExecutionService(session).launch(
                    parent_run=run,
                    parent_task=task,
                    parent_step=step,
                    requested_by_user_id=identity.user_id,
                )
                if launch.status == "waiting_subworkflow":
                    lifecycle.mark_run_waiting_subworkflow(run)
                    return NodeExecutionDefinition(
                        node_type=node_type,
                        status=launch.status,
                        invocation_id=launch.invocation_id,
                    )
                return NodeExecutionDefinition(
                    node_type=node_type,
                    status="ready",
                    result=ToolExecutionResult(
                        status=launch.status,
                        output=launch.output,
                        error=launch.error,
                    ),
                )
            if node_type not in {"tool", "mcp"}:
                raise ApplicationError("Unsupported node type", non_retryable=True)
            context = builder.build_direct_tool_context(run, request)
            name = step.dependencies.get("tool_name")
            arguments = step.dependencies.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise ApplicationError("Invalid node tool definition", non_retryable=True)
            prepared = AgentToolAuthorizer(session).prepare(
                context=context,
                tool_name=name,
                arguments={**arguments, **resolve_workflow_inputs(session, task, step)},
            )
            review = AgentToolExecutionService(
                session,
                settings=self.services.settings,
                secret_service=self.services.secrets(),
            ).review_tool_call(context=context, tool_name=name, arguments=prepared.arguments)
            decision = review.get("decision")
            if decision not in {"allow", "require_approval"}:
                raise ApplicationError("Node tool denied by policy", non_retryable=True)
            approval = session.scalar(
                select(Approval)
                .where(
                    Approval.workspace_id == run.workspace_id,
                    Approval.agent_run_id == run.id,
                    Approval.approval_type.in_(("product.tool", "mcp.tool")),
                )
                .order_by(Approval.created_at.desc())
                .limit(1)
            )
            call_id = f"node:{run.id}:{step.id}"
            if decision == "require_approval" and approval is None:
                approval = ApprovalService(session).create_approval(
                    workspace_id=run.workspace_id,
                    task_id=run.task_id,
                    agent_run_id=run.id,
                    requested_by_agent_profile_id=run.agent_profile_id,
                    approval_type=f"{'mcp' if node_type == 'mcp' else 'product'}.tool",
                    risk_level="medium",
                    payload={
                        **review,
                        "tool_name": name,
                        "arguments_sha256": payload_hash(prepared.arguments),
                    },
                )
                PendingToolInvocationService(session, self.services.secrets()).create_or_get(
                    PendingToolInvocationRequest(
                        workspace_id=run.workspace_id,
                        task_id=run.task_id,
                        agent_run_id=run.id,
                        approval_id=approval.id,
                        tool_call_id=call_id,
                        tool_name=name,
                        tool_kind=node_type,
                        arguments=prepared.arguments,
                        policy_decision=review,
                        idempotency_key=call_id,
                    ),
                )
            if approval is not None:
                if approval.payload.get("tool_name") != name or approval.payload.get(
                    "arguments_sha256"
                ) != payload_hash(prepared.arguments):
                    raise ApplicationError("Approved node arguments changed", non_retryable=True)
                if approval.status == "pending":
                    lifecycle.mark_run_waiting_approval(run)
                    return NodeExecutionDefinition(
                        node_type=node_type,
                        status="waiting_approval",
                        approval_id=approval.id,
                    )
                if approval.status != "approved":
                    return NodeExecutionDefinition(
                        node_type=node_type,
                        status="ready",
                        result=ToolExecutionResult(
                            status="failed",
                            error={
                                "code": "approval_rejected",
                                "message": "Tool approval rejected",
                            },
                        ),
                    )
            return NodeExecutionDefinition(
                node_type=node_type,
                status="ready",
                context=context,
                tool_name=name,
                arguments=prepared.arguments,
                tool_call_id=call_id,
                approval_granted=approval is not None and approval.status == "approved",
            )

    @activity.defn(name="opsmesh.orchestration.complete")
    def complete(self, outcome: NodeExecutionOutcome) -> None:
        with self.services.transaction() as session:
            run = self._run(session, outcome.request)
            if run.status in TERMINAL_NODE_STATUSES:
                return
            ExecutionIdentityService(session).for_run(run.workspace_id, run.id)
            lifecycle = RunOrchestrationService(session)._run_lifecycle()
            result = outcome.result
            if result.status == "waiting_approval":
                lifecycle.mark_run_waiting_approval(run)
            elif result.status == "completed":
                output = result.output or {}
                frozen = authorization_snapshot_for_run(run).get("node_definition")
                schema = frozen.get("output_schema") if isinstance(frozen, dict) else None
                structured = None
                if isinstance(schema, dict):
                    validate_json_value(output, schema, label="workflow step output")
                    structured = AgentStructuredOutput(
                        value=output, schema_name="workflow_result", validated=True
                    )
                lifecycle.mark_run_completed(
                    run,
                    AgentRunResult(
                        final_output=json.dumps(output, ensure_ascii=False),
                        raw_output=output,
                        structured_output=structured,
                    ),
                    outcome.request.requested_by_user_id,
                )
                pending = PendingToolInvocationService(session, self.services.secrets())
                pending.mark_decisions_consumed(
                    workspace_id=run.workspace_id,
                    run_id=run.id,
                    decisions=tuple(
                        decision
                        for decision in pending.decisions_for_run(
                            workspace_id=run.workspace_id, run_id=run.id
                        )
                        if decision.tool_call_id == f"node:{run.id}:{run.task_step_id}"
                    ),
                )
            else:
                error = result.error or {}
                lifecycle.mark_run_failed(
                    run,
                    AgentModelProviderError(
                        code=str(error.get("code") or "orchestration_tool_failed"),
                        message=str(error.get("message") or "Orchestration tool execution failed"),
                        retryable=False,
                    ),
                )

    @activity.defn(name="opsmesh.orchestration.cancel")
    def cancel(self, request: ExecutionRequest) -> None:
        with self.services.transaction() as session:
            run = self._run(session, request)
            if run.status not in TERMINAL_NODE_STATUSES:
                RunOrchestrationService(session)._run_lifecycle().mark_run_cancelled(
                    run,
                    completed_at=datetime.now(UTC),
                )

    @activity.defn(name="opsmesh.orchestration.fail")
    def fail(self, request: ExecutionRequest) -> None:
        with self.services.transaction() as session:
            run = self._run(session, request)
            if run.status not in TERMINAL_NODE_STATUSES:
                RunOrchestrationService(session)._run_lifecycle().mark_run_failed(
                    run,
                    RuntimeError("Orchestration execution failed; check the Temporal history"),
                )
