from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from opsmesh.agents.execution.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeContext,
    AgentRuntimeStructuredOutput,
    AgentRuntimeToolResult,
)
from opsmesh.agents.execution.tools.gateway import AgentToolGateway
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.orchestration.approvals.models import Approval
from opsmesh.orchestration.approvals.pending_tools import (
    PendingToolInvocationRequest,
    PendingToolInvocationService,
)
from opsmesh.orchestration.approvals.service import ApprovalService
from opsmesh.orchestration.approvals.waiting import ApprovalWaitingService
from opsmesh.orchestration.definitions.data import resolve_workflow_inputs
from opsmesh.orchestration.definitions.subworkflows import SubworkflowExecutionService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.tasks.models import Task
from opsmesh.runtime.queues.contracts import JobPayload
from opsmesh.shared.utils import payload_hash

if TYPE_CHECKING:
    from opsmesh.orchestration.runs.execution import RunExecutionService


@dataclass(frozen=True)
class DirectToolCall:
    context: AgentRuntimeContext
    tool_name: str
    arguments: dict[str, object]
    tool_call_id: str
    approval_granted: bool
    node_type: str


@dataclass(frozen=True)
class DirectWorkflowExecutor:
    service: RunExecutionService

    def _complete_direct_result(
        self,
        run: AgentRun,
        result: AgentRuntimeToolResult,
        job: JobPayload,
    ) -> AgentRun:
        if run.status in {"completed", "failed", "cancelled"}:
            return run
        ExecutionIdentityService(self.service.session).for_run(run.workspace_id, run.id)
        if result.status == "waiting_approval":
            self.service.dependencies.lifecycle.mark_run_waiting_approval(run)
        elif result.status == "waiting_subworkflow":
            self.service.dependencies.lifecycle.mark_run_waiting_subworkflow(run)
        elif result.status == "completed":
            output = result.output or {}
            self.service.dependencies.lifecycle.mark_run_completed(
                run,
                AgentRunResult(
                    final_output=json.dumps(output, ensure_ascii=False, default=str),
                    raw_output=output,
                    structured_output=AgentRuntimeStructuredOutput(
                        value=output,
                        schema_name="direct_tool_result",
                        validated=False,
                    ),
                ),
                job.requested_by_user_id,
            )
        else:
            self.service.dependencies.lifecycle.mark_run_failed(
                run,
                ValueError(str((result.error or {}).get("message", "Tool failed"))),
            )
        if result.status in {"completed", "failed"} and self.service.node_type(run) in {
            "tool",
            "mcp",
        }:
            PendingToolInvocationService(
                self.service.session,
                self.service._request_builder().secret_service(),
            ).mark_decisions_consumed(workspace_id=run.workspace_id, run_id=run.id)
        self.service.commit_and_refresh(run)
        return run

    def prepare_node(
        self,
        run: AgentRun,
        job: JobPayload,
        request: AgentRunRequest | None,
        node_type: str | None,
    ) -> DirectToolCall | AgentRuntimeToolResult | None:
        if run.task_step_id is None:
            return None
        from opsmesh.orchestration.tasks.models import TaskStep

        step = self.service.session.get(TaskStep, run.task_step_id)
        if step is None or not isinstance(step.dependencies, dict):
            return None
        raw_node_type = step.dependencies.get("node_type")
        node_type = node_type or (raw_node_type if isinstance(raw_node_type, str) else None)
        if node_type == "approval":
            approval = ApprovalService(self.service.session).create_approval(
                workspace_id=run.workspace_id,
                task_id=run.task_id,
                agent_run_id=run.id,
                requested_by_agent_profile_id=run.agent_profile_id,
                approval_type="workflow.node",
                risk_level="medium",
                payload={
                    "node_type": "approval",
                    "work_package_id": step.work_package_id,
                    "title": step.title,
                    "acceptance_criteria": step.acceptance_criteria,
                },
            )
            ApprovalWaitingService(self.service.session).mark_waiting(
                workspace_id=run.workspace_id, run_id=run.id, task_id=run.task_id
            )
            return AgentRuntimeToolResult(
                status="waiting_approval",
                metadata={"approval_id": str(approval.id), "node_type": "approval"},
            )
        if node_type == "subworkflow":
            if run.task_id is None:
                raise ValueError("Subworkflow run has no parent task")
            parent_task = self.service.session.scalar(
                select(Task).where(
                    Task.workspace_id == run.workspace_id,
                    Task.id == run.task_id,
                )
            )
            if parent_task is None:
                raise ValueError("Subworkflow parent task not found")
            launch = SubworkflowExecutionService(
                self.service.session, queue=self.service.queue
            ).launch(
                parent_run=run,
                parent_task=parent_task,
                parent_step=step,
                requested_by_user_id=job.requested_by_user_id,
            )
            return AgentRuntimeToolResult(
                status=launch.status,
                output=launch.output,
                error=launch.error,
                metadata={
                    "invocation_id": str(launch.invocation_id),
                    "child_task_id": str(launch.child_task_id),
                    "child_run_id": str(launch.child_run_id)
                    if launch.child_run_id is not None
                    else None,
                    "node_type": "subworkflow",
                },
            )
        if node_type not in {"tool", "mcp"}:
            return None
        tool_name = step.dependencies.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name:
            raise ValueError("Direct tool node has no tool name")
        arguments = step.dependencies.get("arguments", {})
        if not isinstance(arguments, dict):
            raise ValueError("Direct tool node arguments must be an object")
        if run.task_id is None:
            raise ValueError("Direct tool run has no parent task")
        parent_task = self.service.session.scalar(
            select(Task).where(
                Task.workspace_id == run.workspace_id,
                Task.id == run.task_id,
            )
        )
        if parent_task is None:
            raise ValueError("Direct tool parent task not found")
        arguments = {
            **{key: value for key, value in arguments.items() if isinstance(key, str)},
            **resolve_workflow_inputs(self.service.session, parent_task, step),
        }
        context: AgentRuntimeContext
        if request is None:
            context = self.service._request_builder().build_direct_tool_context(run, job)
        else:
            context = request.context
        direct_approval = self._direct_tool_approval(run, tool_name)
        approval_status = direct_approval.status if direct_approval is not None else None
        tool_call_id = f"direct:{run.task_step_id}:{tool_name}"
        approved_arguments = (
            self._approved_direct_arguments(context, tool_name, arguments, direct_approval)
            if direct_approval is not None
            else None
        )
        if approval_status == "pending" and direct_approval is not None:
            assert approved_arguments is not None
            self._bind_direct_approval(
                run,
                step.id,
                tool_name,
                node_type,
                approved_arguments,
                tool_call_id,
                direct_approval,
            )
            return AgentRuntimeToolResult(
                status="waiting_approval",
                metadata={"node_type": node_type, "tool_name": tool_name},
            )
        if approval_status == "rejected":
            return AgentRuntimeToolResult(
                status="failed",
                error={
                    "code": "tool_approval_rejected",
                    "message": "Direct tool execution was rejected",
                },
                metadata={"node_type": node_type, "tool_name": tool_name},
            )
        if approval_status == "approved" and direct_approval is not None:
            assert approved_arguments is not None
            self._bind_direct_approval(
                run,
                step.id,
                tool_name,
                node_type,
                approved_arguments,
                tool_call_id,
                direct_approval,
            )
        return DirectToolCall(
            context, tool_name, arguments, tool_call_id, approval_status == "approved", node_type
        )

    def complete_call(
        self, run: AgentRun, job: JobPayload, call: DirectToolCall, result: AgentRuntimeToolResult
    ) -> AgentRun:
        if run.task_step_id is None:
            raise ValueError("Direct tool run has no step")
        context, tool_name, arguments = call.context, call.tool_name, call.arguments
        tool_call_id, node_type = call.tool_call_id, call.node_type
        if result.status == "waiting_approval":
            direct_approval = self._direct_tool_approval(run, tool_name)
            if direct_approval is None or direct_approval.status != "pending":
                raise ValueError("Direct tool approval was not persisted")
            approved_arguments = self._approved_direct_arguments(
                context, tool_name, arguments, direct_approval
            )
            self._bind_direct_approval(
                run,
                run.task_step_id,
                tool_name,
                node_type,
                approved_arguments,
                tool_call_id,
                direct_approval,
            )
        return self._complete_direct_result(run, result, job)

    def _approved_direct_arguments(
        self,
        context: AgentRuntimeContext,
        tool_name: str,
        arguments: dict[str, object],
        approval: Approval,
    ) -> dict[str, object]:
        prepared = AgentToolGateway(self.service.session).prepare(
            context=context,
            tool_name=tool_name,
            arguments=arguments,
        )
        payload = approval.payload if isinstance(approval.payload, dict) else {}
        if payload.get("arguments_sha256") != payload_hash(prepared.arguments):
            raise ValueError("Direct tool arguments changed after approval was requested")
        return prepared.arguments

    def _bind_direct_approval(
        self,
        run: AgentRun,
        step_id: UUID,
        tool_name: str,
        node_type: str,
        arguments: dict[str, object],
        tool_call_id: str,
        approval: Approval,
    ) -> None:
        pending = PendingToolInvocationService(
            self.service.session,
            self.service._request_builder().secret_service(),
        )
        invocation = pending.create_or_get(
            PendingToolInvocationRequest(
                workspace_id=run.workspace_id,
                task_id=run.task_id,
                agent_run_id=run.id,
                approval_id=approval.id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_kind=node_type,
                arguments=arguments,
                policy_decision={"source": "workflow.direct_tool"},
                idempotency_key=f"direct:{run.id}:{step_id}",
            )
        )
        if approval.status == "approved" and invocation.status == "pending":
            pending.record_decision(
                workspace_id=run.workspace_id,
                approval_id=approval.id,
                status="approved",
            )

    def _direct_tool_approval(self, run: AgentRun, tool_name: str) -> Approval | None:
        approvals = self.service.session.scalars(
            select(Approval)
            .where(
                Approval.workspace_id == run.workspace_id,
                Approval.agent_run_id == run.id,
                Approval.approval_type.in_(("product.tool", "mcp.tool")),
            )
            .order_by(Approval.created_at.desc(), Approval.id.desc())
        )
        has_prior_tool_approval = False
        for approval in approvals:
            has_prior_tool_approval = True
            payload = approval.payload
            if isinstance(payload, dict) and payload.get("tool_name") == tool_name:
                return approval
        if has_prior_tool_approval:
            raise ValueError("Direct tool changed after approval was requested")
        return None
