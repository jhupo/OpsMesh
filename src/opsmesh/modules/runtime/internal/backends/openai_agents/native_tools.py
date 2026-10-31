"""Local SDK tools execute registered neutral capabilities under per-call admission."""

import inspect
from typing import Any

from agents import ApplyPatchTool, ShellTool
from agents.editor import ApplyPatchResult
from agents.tool import ShellCallOutcome, ShellCommandOutput, ShellResult

from opsmesh.framework.agents import (
    AgentExecutionContext,
    AgentSpecification,
    ApplyPatchToolBinding,
    ComputerToolBinding,
    FilePatchOperation,
    FilePatchResult,
    NativeToolBinding,
    ShellExecutionRequest,
    ShellExecutionResult,
    ShellToolBinding,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import RuntimeExtensions


async def authorize_native(context: AgentExecutionContext, binding: NativeToolBinding) -> None:
    authorize = context.services.get("authorize_native_tool")
    if authorize is None:
        raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Native tools require per-call authorization")
    value = authorize(binding)
    if inspect.isawaitable(value):
        await value


def shell_tool(binding: ShellToolBinding, extensions: RuntimeExtensions) -> ShellTool:
    async def execute(request: Any) -> ShellResult:
        context = request.ctx_wrapper.context
        await authorize_native(context, binding)
        action = request.data.action
        arguments = ShellExecutionRequest(
            call_id=request.data.call_id,
            commands=tuple(action.commands),
            timeout_ms=action.timeout_ms,
            max_output_length=action.max_output_length,
        )
        result = extensions.shell(binding.executor_ref)(context, arguments)
        result = await result if inspect.isawaitable(result) else result
        if not isinstance(result, ShellExecutionResult) or len(result.outputs) != len(
            action.commands
        ):
            raise OpsMeshError(
                "EXTENSION_INVALID", "Shell executor must return one result per command"
            )
        return ShellResult(
            output=[
                ShellCommandOutput(
                    stdout=item.stdout,
                    stderr=item.stderr,
                    command=command,
                    outcome=ShellCallOutcome(
                        type="timeout" if item.timed_out else "exit", exit_code=item.exit_code
                    ),
                )
                for command, item in zip(action.commands, result.outputs, strict=True)
            ]
        )

    return ShellTool(executor=execute, needs_approval=binding.requires_approval)


class RegisteredPatchEditor:
    def __init__(self, binding: ApplyPatchToolBinding, extensions: RuntimeExtensions) -> None:
        self.binding, self.extensions = binding, extensions

    async def apply(self, operation: Any) -> ApplyPatchResult:
        if operation.ctx_wrapper is None:
            raise OpsMeshError("CONTEXT_UNAVAILABLE", "Patch execution requires a run context")
        context = operation.ctx_wrapper.context
        await authorize_native(context, self.binding)
        request = FilePatchOperation(
            type=operation.type, path=operation.path, diff=operation.diff, move_to=operation.move_to
        )
        result = self.extensions.patch(self.binding.editor_ref)(context, request)
        result = await result if inspect.isawaitable(result) else result
        if not isinstance(result, FilePatchResult):
            raise OpsMeshError("EXTENSION_INVALID", "Patch editor must return FilePatchResult")
        return ApplyPatchResult(status=result.status, output=result.output)

    async def create_file(self, operation: Any) -> Any:
        return await self.apply(operation)

    async def update_file(self, operation: Any) -> Any:
        return await self.apply(operation)

    async def delete_file(self, operation: Any) -> Any:
        return await self.apply(operation)


def build_native_tools(spec: AgentSpecification, extensions: RuntimeExtensions) -> list[Any]:
    from .computer import build_computer_tool

    tools: list[Any] = []
    for binding in spec.native_tools:
        if isinstance(binding, ShellToolBinding):
            tools.append(shell_tool(binding, extensions))
        elif isinstance(binding, ApplyPatchToolBinding):
            tools.append(
                ApplyPatchTool(
                    editor=RegisteredPatchEditor(binding, extensions),
                    needs_approval=binding.requires_approval,
                )
            )
        elif isinstance(binding, ComputerToolBinding):
            tools.append(build_computer_tool(binding, extensions))
    return tools
