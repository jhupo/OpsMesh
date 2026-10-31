"""Stable restore graphs with a fresh sandbox for each delegated tool invocation."""

import asyncio
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any

from agents import RunContextWrapper, RunState
from agents.agent_tool_state import (
    get_agent_tool_state_scope,
    peek_agent_tool_run_result,
    record_agent_tool_resume_state,
    set_agent_tool_state_scope,
)
from agents.run_internal.agent_tool_configuration import agent_tool_configuration_run
from agents.sandbox.capabilities.tools import (
    ExecCommandTool,
    SandboxApplyPatchTool,
    ViewImageTool,
    WriteStdinTool,
)
from agents.sandbox.workspace_paths import SandboxWorkspaceScope

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError


class RestoreOnlySession:
    """Tool identity/schema reconstruction only; execution requires a real session."""

    def __getattr__(self, name: str) -> Any:
        raise OpsMeshError(
            "ENVIRONMENT_UNAVAILABLE", "Restore template cannot execute sandbox operations"
        )


def restoration_tools(spec: AgentSpecification, session: Any = None) -> list[Any]:
    assert spec.sandbox is not None
    session = session if session is not None else RestoreOnlySession()
    scope = SandboxWorkspaceScope(cwd=PurePosixPath(spec.sandbox.cwd) if spec.sandbox.cwd else None)
    kinds = set(spec.sandbox.capabilities) | {
        item.get("type") for item in spec.sandbox.capability_options
    }
    result: list[Any] = []
    if "shell" in kinds:
        result.extend(
            [
                ExecCommandTool(session=session, user=spec.sandbox.run_as, workspace_scope=scope),
                WriteStdinTool(session=session),
            ]
        )
    if "filesystem" in kinds:
        result.extend(
            [
                SandboxApplyPatchTool(
                    session=session, user=spec.sandbox.run_as, workspace_scope=scope
                ),
                ViewImageTool(session=session, user=spec.sandbox.run_as, workspace_scope=scope),
            ]
        )
    return result


def isolate_agent_tool(
    tool: Any,
    binding: Any,
    child_spec: AgentSpecification,
    parent_spec: AgentSpecification,
    extensions: Any,
    model_resolver: Any,
    tasks: set[asyncio.Task[Any]],
) -> Any:
    async def execute(wrapper: Any, arguments: str) -> Any:
        from .agent_builder import AgentGraphBuilder
        from .config import build_run_config
        from .tools import build_agent_tool
        from .usage import publish_unknown_usage

        prepare = wrapper.context.services.get("prepare_delegated_sandbox")
        if prepare is None:
            raise OpsMeshError(
                "DEPENDENCY_UNAVAILABLE", "Delegated sandbox lifecycle is unavailable"
            )
        child_context = await prepare(child_spec, wrapper.context, wrapper.tool_call_id)
        builder = AgentGraphBuilder(
            extensions,
            child_context,
            lambda spec: build_run_config(spec, child_context),
            model_resolver,
        )
        try:
            target = await builder.build(child_spec)
            # RunState deserializes pending tool objects before Runner binds capabilities.
            # Give it real session-bound identities; Runner will refresh capability tools.
            scope = get_agent_tool_state_scope(wrapper)
            pending = peek_agent_tool_run_result(wrapper.tool_call, scope_id=scope)
            if pending is not None:
                original_tools = target.tools
                native_tools = restoration_tools(
                    child_spec, child_context.services["openai_sandbox_session"]
                )
                target.tools = [*original_tools, *native_tools]
                old_state = pending.to_state()
                try:
                    # Keep restore-only inventory checks out of the enclosing parent run.
                    # The child Runner opens its own immutable configuration scope below.
                    with agent_tool_configuration_run(target):
                        state = await RunState.from_json(
                            target,
                            old_state.to_json(
                                context_serializer=lambda _: child_context.snapshot(),
                                include_tracing_api_key=False,
                            ),
                            context_override=RunContextWrapper(child_context),
                        )
                finally:
                    target.tools = original_tools
                record_agent_tool_resume_state(
                    wrapper.tool_call,
                    state,
                    scope_id=scope,
                    approval_items=old_state.get_interruptions(),
                )
            native = build_agent_tool(
                binding,
                target,
                child_spec,
                build_run_config(child_spec, child_context),
                extensions,
                parent_spec,
            )
            nested_wrapper = replace(wrapper, context=child_context)
            set_agent_tool_state_scope(nested_wrapper, scope)
            result = await native.on_invoke_tool(nested_wrapper, arguments)
            await child_context.services["checkpoint_resources"]()
            return result
        finally:
            try:
                await publish_unknown_usage(child_context)
            finally:
                try:
                    await builder.close()
                finally:
                    await child_context.services["cleanup_resources"]()

    async def invoke(wrapper: Any, arguments: str) -> Any:
        task = asyncio.current_task()
        assert task is not None
        tasks.add(task)
        try:
            return await execute(wrapper, arguments)
        finally:
            tasks.discard(task)

    tool.on_invoke_tool = invoke
    return tool
