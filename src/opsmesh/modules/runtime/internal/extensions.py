"""Owned, in-process runtime code declarations."""

from functools import wraps
from typing import Any, TypeVar, cast

from opsmesh.framework.agents import BackendOutcome, OpsMeshAgentBackend
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.plugin import OpsMeshPlugin, get_current_plugin
from opsmesh.modules.runtime.api import (
    AgentInputBuilder,
    BackendFactory,
    ComputerFactory,
    ComputerSafetyPolicy,
    Guardrail,
    HandoffFilter,
    Hook,
    HostedMCPApprovalPolicy,
    Instruction,
    MCPFailureFormatter,
    OutputExtractor,
    PatchEditor,
    PromptFactory,
    PromptTemplateDefinition,
    ShellExecutor,
    ToolCompletion,
    Visibility,
)

T = TypeVar("T")


def require_plugin(owner: OpsMeshPlugin | None) -> None:
    if owner is not None and not owner.enabled:
        raise OpsMeshError("PLUGIN_DISABLED", "Runtime extension belongs to a disabled plugin")


class OwnedAgentBackend:
    def __init__(self, backend: OpsMeshAgentBackend, owner: OpsMeshPlugin | None) -> None:
        self.backend, self.owner = backend, owner
        self.id, self.version, self.features = backend.id, backend.version, backend.features

    async def run(self, *args: Any, **kwargs: Any) -> BackendOutcome:
        require_plugin(self.owner)
        return await self.backend.run(*args, **kwargs)


class RuntimeExtensionRegistry:
    def __init__(self) -> None:
        self._backends: dict[str, BackendFactory] = {}
        self._guardrails: dict[str, Guardrail] = {}
        self._hooks: dict[str, Hook] = {}
        self._instructions: dict[str, Instruction] = {}
        self._prompts: dict[str, PromptFactory] = {}
        self._prompt_templates: dict[str, Any] = {}
        self._visibility: dict[str, Visibility] = {}
        self._handoff_filters: dict[str, HandoffFilter] = {}
        self._output_extractors: dict[str, OutputExtractor] = {}
        self._output_types: dict[str, Any] = {}
        self._tool_completions: dict[str, ToolCompletion] = {}
        self._agent_inputs: dict[str, AgentInputBuilder] = {}
        self._mcp_approvals: dict[str, HostedMCPApprovalPolicy] = {}
        self._mcp_failure_formatters: dict[str, MCPFailureFormatter] = {}
        self._shells: dict[str, ShellExecutor] = {}
        self._patch_editors: dict[str, PatchEditor] = {}
        self._computers: dict[str, ComputerFactory] = {}
        self._computer_safety_policies: dict[str, ComputerSafetyPolicy] = {}
        self._frozen = False

    def _register(self, store: dict[str, T], name: str, value: T) -> None:
        if self._frozen:
            raise OpsMeshError("registry_frozen", "AgentRuntime declarations are frozen")
        if not name or name in store:
            raise OpsMeshError("conflict", "Duplicate or empty runtime declaration")
        owner = get_current_plugin()

        @wraps(cast(Any, value))
        def guarded(*args: Any, **kwargs: Any) -> Any:
            require_plugin(owner)
            return cast(Any, value)(*args, **kwargs)

        wrapped = cast(T, guarded)
        cast(Any, guarded).__opsmesh_plugin__ = owner
        store[name] = wrapped
        if owner is not None:

            def revoke() -> None:
                if store.get(name) is wrapped:
                    store.pop(name)

            owner.own(revoke)

    @staticmethod
    def _get(store: dict[str, T], name: str) -> T:
        try:
            value = store[name]
            require_plugin(getattr(value, "__opsmesh_plugin__", None))
            return value
        except KeyError as exc:
            raise OpsMeshError(
                "extension_not_found", f"Unknown runtime declaration: {name}"
            ) from exc

    def register_backend(self, name: str, factory: BackendFactory) -> None:
        owner = get_current_plugin()

        def create() -> OpsMeshAgentBackend:
            return OwnedAgentBackend(factory(), owner)

        self._register(self._backends, name, create)

    def register_guardrail(self, ref: str, callback: Guardrail) -> None:
        self._register(self._guardrails, ref, callback)

    def register_hook(self, ref: str, callback: Hook) -> None:
        self._register(self._hooks, ref, callback)

    def register_instruction(self, ref: str, callback: Instruction) -> None:
        self._register(self._instructions, ref, callback)

    def register_prompt_template(self, template: PromptTemplateDefinition) -> None:
        snapshot = template.model_dump(mode="json")
        self._register(
            self._prompt_templates,
            template.ref,
            lambda: PromptTemplateDefinition.model_validate(snapshot),
        )

    def prompt_template(self, ref: str) -> PromptTemplateDefinition:
        return cast(PromptTemplateDefinition, self._get(self._prompt_templates, ref)())

    def register_prompt(self, ref: str, callback: PromptFactory) -> None:
        self._register(self._prompts, ref, callback)

    def register_visibility(self, ref: str, callback: Visibility) -> None:
        self._register(self._visibility, ref, callback)

    def register_handoff_filter(self, ref: str, callback: HandoffFilter) -> None:
        self._register(self._handoff_filters, ref, callback)

    def register_output_extractor(self, ref: str, callback: OutputExtractor) -> None:
        self._register(self._output_extractors, ref, callback)

    def backend(self, name: str) -> OpsMeshAgentBackend:
        return self._get(self._backends, name)()

    def guardrail(self, ref: str) -> Guardrail:
        return self._get(self._guardrails, ref)

    def hook(self, ref: str) -> Hook:
        return self._get(self._hooks, ref)

    def instruction(self, ref: str) -> Instruction:
        return self._get(self._instructions, ref)

    def prompt(self, ref: str) -> PromptFactory:
        return self._get(self._prompts, ref)

    def visibility(self, ref: str) -> Visibility:
        return self._get(self._visibility, ref)

    def handoff_filter(self, ref: str) -> HandoffFilter:
        return self._get(self._handoff_filters, ref)

    def output_extractor(self, ref: str) -> OutputExtractor:
        return self._get(self._output_extractors, ref)

    def register_output_type(self, ref: str, callback: Any) -> None:
        self._register(self._output_types, ref, lambda: callback)

    def output_type(self, ref: str) -> Any:
        return self._get(self._output_types, ref)()

    def register_tool_completion(self, ref: str, callback: ToolCompletion) -> None:
        self._register(self._tool_completions, ref, callback)

    def tool_completion(self, ref: str) -> ToolCompletion:
        return self._get(self._tool_completions, ref)

    def register_agent_input(self, ref: str, callback: AgentInputBuilder) -> None:
        self._register(self._agent_inputs, ref, callback)

    def agent_input(self, ref: str) -> AgentInputBuilder:
        return self._get(self._agent_inputs, ref)

    def register_mcp_failure_formatter(self, ref: str, callback: MCPFailureFormatter) -> None:
        self._register(self._mcp_failure_formatters, ref, callback)

    def mcp_failure_formatter(self, ref: str) -> MCPFailureFormatter:
        return self._get(self._mcp_failure_formatters, ref)

    def register_mcp_approval(self, ref: str, callback: HostedMCPApprovalPolicy) -> None:
        self._register(self._mcp_approvals, ref, callback)

    def mcp_approval(self, ref: str) -> HostedMCPApprovalPolicy:
        return self._get(self._mcp_approvals, ref)

    def register_shell(self, ref: str, callback: ShellExecutor) -> None:
        self._register(self._shells, ref, callback)

    def shell(self, ref: str) -> ShellExecutor:
        return self._get(self._shells, ref)

    def register_patch(self, ref: str, callback: PatchEditor) -> None:
        self._register(self._patch_editors, ref, callback)

    def patch(self, ref: str) -> PatchEditor:
        return self._get(self._patch_editors, ref)

    def register_computer(self, ref: str, callback: ComputerFactory) -> None:
        self._register(self._computers, ref, callback)

    def computer(self, ref: str) -> ComputerFactory:
        return self._get(self._computers, ref)

    def register_computer_safety(self, ref: str, callback: ComputerSafetyPolicy) -> None:
        self._register(self._computer_safety_policies, ref, callback)

    def computer_safety(self, ref: str) -> ComputerSafetyPolicy:
        return self._get(self._computer_safety_policies, ref)

    def freeze(self) -> None:
        self._frozen = True
