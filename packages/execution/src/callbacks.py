"""Plugin-owned SDK callbacks using framework dependency injection."""

import logging
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from inspect import Parameter
from typing import Any

from opsmesh.dependencies import Dependent, Param
from opsmesh.dependencies.context import ContextParam
from opsmesh.dependencies.params import DefaultParam, DependParam
from opsmesh.matchers.matcher import current_matcher
from opsmesh.plugins.invocation import (
    PluginCall,
    hold_continuation,
    invocation_scope,
)
from opsmesh.plugins.model import Plugin
from opsmesh.plugins.registry import (
    get_current_plugin,
    get_plugin,
    get_plugin_by_module_name,
    plugin_scope,
    track_registration,
)

logger = logging.getLogger(__name__)

_PARAMETERS = {
    "before_run": ("agent", "input", "run_config", "trace_id"),
    "on_run_end": ("result", "error", "trace_id"),
    "on_agent_start": ("context", "agent"),
    "on_agent_end": ("context", "agent", "output"),
    "on_handoff": ("context", "from_agent", "to_agent"),
    "on_llm_start": ("context", "agent", "system_prompt", "input_items"),
    "on_llm_end": ("context", "agent", "response"),
    "on_tool_start": ("context", "agent", "tool"),
    "on_tool_end": ("context", "agent", "tool", "result"),
}


class _SDKParameter(Param):
    """Pass native SDK objects without reconstructing their dataclasses."""

    names: frozenset[str] = frozenset()

    @classmethod
    def _check_param(cls, param: Parameter, allow_types) -> Param | None:
        if param.name in cls.names:
            return cls(default=param.name)
        return None

    async def _solve(self, *, sdk_values: dict[str, Any], **kwargs: Any) -> Any:
        return sdk_values[self.default]


class _SDKDependency(DependParam):
    """Admit only dependency tasks created by the callback bridge."""

    async def _solve(self, *, callback_call: PluginCall, **kwargs: Any) -> Any:
        with invocation_scope(callback_call):
            return await super()._solve(callback_call=callback_call, **kwargs)


@dataclass(eq=False, frozen=True)
class _Callback:
    event: str
    owner: Plugin
    dependent: Dependent[Any]


_callbacks: list[_Callback] = []


def _register(event: str, function: Callable[..., Any]) -> Callable[..., Any]:
    owner = get_current_plugin() or get_plugin_by_module_name(
        function.__module__
    )
    if owner is None:
        raise RuntimeError("Execution callbacks must belong to a loaded plugin")
    parameters = type(
        "SDKCallbackParameter",
        (_SDKParameter,),
        {"names": frozenset(_PARAMETERS[event])},
    )
    callback = _Callback(
        event,
        owner,
        Dependent.parse(
            call=function,
            allow_types=(
                ContextParam,
                _SDKDependency,
                parameters,
                DefaultParam,
            ),
        ),
    )
    _callbacks.append(callback)

    def remove() -> None:
        if callback in _callbacks:
            _callbacks.remove(callback)

    track_registration(remove, function)
    return function


def on_agent_start(function: Callable[..., Any]) -> Callable[..., Any]:
    """Observe each native agent start, including handoffs."""
    return _register("on_agent_start", function)


def on_agent_end(function: Callable[..., Any]) -> Callable[..., Any]:
    """Observe the native agent's final output."""
    return _register("on_agent_end", function)


def on_llm_start(function: Callable[..., Any]) -> Callable[..., Any]:
    """Observe a model request; Context() injects the caller identity."""
    return _register("on_llm_start", function)


def on_llm_end(function: Callable[..., Any]) -> Callable[..., Any]:
    """Observe the native ModelResponse, including its Usage."""
    return _register("on_llm_end", function)


def on_tool_start(function: Callable[..., Any]) -> Callable[..., Any]:
    """Observe a local SDK tool invocation."""
    return _register("on_tool_start", function)


def on_tool_end(function: Callable[..., Any]) -> Callable[..., Any]:
    """Observe a local SDK tool's result."""
    return _register("on_tool_end", function)


def on_handoff(function: Callable[..., Any]) -> Callable[..., Any]:
    """Observe the native transfer between agents."""
    return _register("on_handoff", function)


def before_run(function: Callable[..., Any]) -> Callable[..., Any]:
    """Check or reserve resources before invoking the native Runner."""
    return _register("before_run", function)


def on_run_end(function: Callable[..., Any]) -> Callable[..., Any]:
    """Observe native results or errors without replacing the run outcome."""
    return _register("on_run_end", function)


def _owners(plugin_id: str | None) -> set[str]:
    """Select the caller and its declared dependency closure."""
    result: set[str] = set()
    pending = [plugin_id] if plugin_id else []
    while pending:
        identifier = pending.pop()
        if identifier in result:
            continue
        result.add(identifier)
        plugin = get_plugin(identifier)
        if plugin is None:
            raise ValueError(f"Unknown execution owner: {identifier}")
        pending.extend(plugin.dependencies)
    return result


@dataclass
class _OwnerResources:
    """Dependencies shared only by callbacks of one plugin."""

    owner: Plugin
    stack: AsyncExitStack
    cache: dict[Any, Any]
    call: PluginCall
    admission: AsyncExitStack

    async def close(self, error: BaseException | None) -> None:
        """Release admission only after every dependency closes successfully."""
        with plugin_scope(self.owner), invocation_scope(self.call):
            await self.stack.__aexit__(
                type(error) if error else None,
                error,
                error.__traceback__ if error else None,
            )
            await self.admission.aclose()


class ExecutionCallbacks:
    """Own the caller's SDK callbacks and run-scoped dependency stack."""

    def __init__(self, plugin_id: str | None) -> None:
        """Snapshot declarations from the caller and its dependencies."""
        matcher = current_matcher.get(None)
        owner_id = (
            matcher._source.plugin_id if matcher and matcher._source else None
        )
        if plugin_id and owner_id and owner_id != plugin_id:
            raise ValueError("Execution plugin differs from handling plugin")
        self.plugin_id = plugin_id or owner_id
        if self.plugin_id is None:
            raise ValueError("Background execution requires plugin_id")
        owners = _owners(self.plugin_id)
        execution = get_plugin_by_module_name(__name__)
        if execution is None or execution.id_ not in owners:
            raise ValueError("Execution owner must require execution")
        self._callbacks = tuple(
            callback for callback in _callbacks if callback.owner.id_ in owners
        )
        self._resources: dict[str, _OwnerResources] = {}
        self._call: PluginCall | None = None

    def _new_resources(self, owner: Plugin) -> _OwnerResources:
        if self._call is None:
            raise RuntimeError("Execution callbacks require an admitted call")
        admission = AsyncExitStack()
        call = hold_continuation(self._call, owner.id_, admission)
        return _OwnerResources(owner, AsyncExitStack(), {}, call, admission)

    def _resources_for(self, owner: Plugin) -> _OwnerResources:
        if owner.id_ not in self._resources:
            self._resources[owner.id_] = self._new_resources(owner)
        return self._resources[owner.id_]

    async def emit(self, event: str, *args: Any) -> None:
        """Preserve native callback exceptions and original SDK objects."""
        values = dict(zip(_PARAMETERS[event], args, strict=True))
        await self._dispatch(event, values, values["context"].context)

    async def starting(
        self, values: dict[str, Any], context: Any, *, call: PluginCall
    ) -> None:
        """Reject the execution if any prerequisite callback fails."""
        self._call = call
        await self._dispatch("before_run", values, context)

    async def _dispatch(self, event, values, context) -> None:
        for callback in self._callbacks:
            if callback.event == event:
                resources = self._resources_for(callback.owner)
                with (
                    plugin_scope(callback.owner),
                    invocation_scope(resources.call),
                ):
                    await callback.dependent(
                        stack=resources.stack,
                        dependency_cache=resources.cache,
                        callback_call=resources.call,
                        invocation_context=context,
                        sdk_values=values,
                    )

    async def finished(
        self, values: dict[str, Any], context: Any, timeout: float
    ) -> None:
        """Notify each observer independently within its cleanup budget."""
        import anyio

        interrupted: BaseException | None = None
        for callback in self._callbacks:
            if callback.event != "on_run_end":
                continue
            resources = self._new_resources(callback.owner)
            failure = None
            try:
                with (
                    plugin_scope(callback.owner),
                    invocation_scope(resources.call),
                    anyio.fail_after(timeout, shield=True),
                ):
                    await callback.dependent(
                        stack=resources.stack,
                        dependency_cache=resources.cache,
                        callback_call=resources.call,
                        invocation_context=context,
                        sdk_values=values,
                    )
            except BaseException as error:
                failure = error
                if not isinstance(error, Exception):
                    interrupted = interrupted or error
                else:
                    logger.exception("Execution end observer failed")
            finally:
                try:
                    with anyio.fail_after(timeout, shield=True):
                        await resources.close(failure)
                except BaseException as error:
                    if not isinstance(error, Exception):
                        interrupted = interrupted or error
                    else:
                        logger.exception(
                            "Execution end observer cleanup failed"
                        )
        if interrupted is not None:
            raise interrupted

    async def close(self, error: BaseException | None = None) -> None:
        """Release SDK callback dependencies with the original exception."""
        failure = error
        resources, self._resources = tuple(self._resources.values()), {}
        for resource in reversed(resources):
            try:
                await resource.close(failure)
            except BaseException as cleanup_error:
                if failure is None:
                    failure = cleanup_error
                elif cleanup_error is not failure:
                    failure.add_note(
                        f"Callback cleanup also failed: {cleanup_error!r}"
                    )
        if failure is not None and failure is not error:
            raise failure
