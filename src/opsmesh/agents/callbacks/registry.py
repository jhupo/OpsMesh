"""Per-application factories, with a separate namespace for each callback contract."""

from collections.abc import Callable
from inspect import isawaitable, iscoroutine
from typing import Any, Literal

from agents.guardrail import InputGuardrail, OutputGuardrail
from agents.lifecycle import AgentHooksBase, RunHooksBase
from pydantic import JsonValue

from opsmesh.agents.config.callbacks import CallbackRef

CallbackKind = Literal[
    "instructions",
    "prompt",
    "input_guardrail",
    "output_guardrail",
    "agent_hooks",
    "run_hooks",
    "tool_use_behavior",
    "tool_enabled",
    "tool_approval",
    "tool_error",
    "output_extractor",
    "stream",
    "input_type",
    "output_type",
    "input_builder",
    "on_handoff",
    "handoff_filter",
    "capability",
    "configure_tools",
    "skill_source",
]
CallbackFactory = Callable[[dict[str, JsonValue]], Any]


class CallbackRegistry:
    def __init__(self) -> None:
        self._factories: dict[tuple[CallbackKind, str], CallbackFactory] = {}
        self._revisions: dict[tuple[CallbackKind, str], str | None] = {}

    def register(
        self,
        kind: CallbackKind,
        callback_id: str,
        factory: CallbackFactory,
        *,
        replace: bool = False,
        revision: str | None = None,
    ) -> None:
        key = kind, callback_id
        if not callback_id or not callable(factory):
            raise ValueError("A callback needs a nonempty ID and callable factory")
        if revision is not None and not revision.strip():
            raise ValueError("A callback revision must be nonempty")
        if key in self._factories and not replace:
            raise ValueError(f"Callback already registered: {kind}/{callback_id}")
        self._factories[key] = factory
        self._revisions[key] = revision

    def revision(self, kind: CallbackKind, reference: CallbackRef) -> str | None:
        return self._revisions.get((kind, reference.callback_id))

    def resolve(self, kind: CallbackKind, reference: CallbackRef) -> Any:
        factory = self._factories.get((kind, reference.callback_id))
        if factory is None:
            raise KeyError(f"Callback is not registered: {kind}/{reference.callback_id}")
        value = factory(reference.model_copy(deep=True).config)
        if value is None or isawaitable(value):
            if iscoroutine(value):
                value.close()
            raise TypeError("Callback factories must synchronously return a registered SDK object")
        contracts: dict[CallbackKind, type[Any]] = {
            "input_guardrail": InputGuardrail,
            "output_guardrail": OutputGuardrail,
            "agent_hooks": AgentHooksBase,
            "run_hooks": RunHooksBase,
            "input_type": type,
        }
        contract = contracts.get(kind)
        if contract is not None and not isinstance(value, contract):
            raise TypeError(f"Callback factory returned the wrong object for {kind}")
        callable_kinds = {
            "instructions",
            "prompt",
            "tool_use_behavior",
            "tool_enabled",
            "tool_approval",
            "tool_error",
            "output_extractor",
            "stream",
            "input_builder",
            "on_handoff",
            "handoff_filter",
            "configure_tools",
        }
        if kind in callable_kinds and not callable(value):
            raise TypeError(f"Callback factory must return a callable for {kind}")
        return value

    def remove(self, kind: CallbackKind, callback_id: str) -> None:
        del self._factories[kind, callback_id]
        del self._revisions[kind, callback_id]

    def list(self, kind: CallbackKind) -> list[str]:
        return sorted(identifier for category, identifier in self._factories if category == kind)
