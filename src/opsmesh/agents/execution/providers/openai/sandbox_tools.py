"""Attach product policy to native SDK tools through capability configuration callbacks."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from agents import CustomTool, FunctionTool, RunContextWrapper
from agents.sandbox.capabilities.filesystem import FilesystemToolSet
from agents.sandbox.capabilities.shell import ShellToolSet
from agents.tool_context import ToolContext
from pydantic import TypeAdapter
from temporalio import workflow
from temporalio.common import RetryPolicy

from opsmesh.agents.execution.contracts import AgentRunRequest
from opsmesh.agents.execution.providers.openai.activity_policy import AGENT_ACTIVITY_RETRY_POLICY
from opsmesh.agents.execution.providers.openai.sandbox_activities import AgentSandboxToolOperation


def configure_sandbox_tools(
    request: AgentRunRequest, reviews: dict[str, dict[str, object]]
) -> Callable[[ShellToolSet | FilesystemToolSet], None]:
    def configure(bundle: ShellToolSet | FilesystemToolSet) -> None:
        if isinstance(bundle, ShellToolSet):
            _govern(bundle.exec_command, request, reviews)
            if bundle.write_stdin is not None:
                _govern(bundle.write_stdin, request, reviews)
        else:
            _govern(bundle.view_image, request, reviews)
            native = bundle.apply_patch
            # The SDK's patch wrapper exposes per-operation approvals. Product
            # approvals bind the entire raw patch to one durable native call ID.
            # Keep its public invocation and grammar while using CustomTool's
            # public raw-input approval callback.
            tool = CustomTool(
                name=native.name,
                description=native.description,
                format=native.format,
                on_invoke_tool=native.on_invoke_tool,
            )
            _govern(tool, request, reviews)
            bundle.apply_patch = tool  # type: ignore[assignment]

    return configure


def _arguments(tool: FunctionTool | CustomTool, raw: str) -> dict[str, object]:
    if isinstance(tool, CustomTool):
        return {"patch": raw}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Native sandbox arguments must be an object")
    return value


def _govern(
    tool: FunctionTool | CustomTool,
    request: AgentRunRequest,
    reviews: dict[str, dict[str, object]],
) -> None:
    native_invoke = tool.on_invoke_tool

    async def review(
        context: RunContextWrapper[Any], arguments: dict[str, object] | str, call_id: str
    ) -> bool:
        value = _arguments(tool, arguments) if isinstance(arguments, str) else arguments
        decision = await workflow.execute_activity(
            "opsmesh.agent.sandbox_review",
            AgentSandboxToolOperation(
                context=request.context, tool_name=tool.name, arguments=value, tool_call_id=call_id
            ),
            result_type=dict[str, object],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
        )
        reviews[call_id] = decision
        if decision.get("decision") not in {"allow", "require_approval"}:
            raise ValueError("Native sandbox tool was denied by policy")
        return bool(decision["decision"] == "require_approval")

    async def execute(context: ToolContext[Any], raw: str) -> Any:
        operation = AgentSandboxToolOperation(
            context=request.context,
            tool_name=tool.name,
            arguments=_arguments(tool, raw),
            tool_call_id=context.tool_call_id,
            approval_granted=context.is_tool_approved(tool.name, context.tool_call_id) is True,
        )
        cached = await workflow.execute_activity(
            "opsmesh.agent.sandbox_admit",
            operation,
            result_type=dict[str, object],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        if cached is not None:
            if cached.get("status") != "completed":
                raise ValueError("Native sandbox tool outcome cannot be replayed")
            return cached.get("output")
        try:
            result = await native_invoke(context, raw)
        except BaseException:
            await asyncio.shield(_finish(operation.model_copy(update={"failed": True})))
            raise
        await _finish(
            operation.model_copy(
                update={
                    "result": TypeAdapter(Any).dump_python(result, mode="json"),
                }
            )
        )
        return result

    async def invoke(context: ToolContext[Any], raw: str) -> Any:
        task = asyncio.current_task()
        pending = request.sdk_sandbox_tasks
        if task is not None and pending is not None:
            pending[task] = None
        try:
            return await execute(context, raw)
        finally:
            if task is not None and pending is not None:
                pending.pop(task, None)

    tool.needs_approval = review
    tool.on_invoke_tool = invoke


async def _finish(operation: AgentSandboxToolOperation) -> None:
    await workflow.execute_activity(
        "opsmesh.agent.sandbox_finish",
        operation,
        start_to_close_timeout=timedelta(seconds=30),
        retry_policy=AGENT_ACTIVITY_RETRY_POLICY,
    )
