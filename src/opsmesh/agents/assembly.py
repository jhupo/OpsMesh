"""Build native SDK Agents within a caller-owned async resource lifespan."""

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents import Agent, ModelSettings, handoff
from agents.handoffs import Handoff
from agents.models.interface import Model
from agents.sandbox import SandboxAgent
from agents.sandbox.capabilities import Skills
from agents.sandbox.entries import BaseEntry, Dir

from opsmesh.agents.callbacks import CallbackRegistry
from opsmesh.agents.callbacks.registry import CallbackKind
from opsmesh.agents.config.agent import AgentConfig, BaseAgentConfig, PromptConfig, StopAtTools
from opsmesh.agents.config.callbacks import CallbackRef
from opsmesh.agents.config.manifest import EntryRef
from opsmesh.agents.config.model import ModelRef, model_settings
from opsmesh.agents.config.sandbox import SandboxAgentConfig
from opsmesh.agents.config.tools import AgentToolRef
from opsmesh.agents.providers import ProviderRegistry, create_model
from opsmesh.agents.providers.factory import CredentialResolver
from opsmesh.agents.resolvers.agents import resolve_agents
from opsmesh.agents.resolvers.mcp import MCPResolver, resolve_mcp
from opsmesh.agents.resolvers.output import resolve_output
from opsmesh.agents.resolvers.sandbox import (
    ManifestResolver,
    resolve_capabilities,
    resolve_manifest,
)
from opsmesh.agents.skills import SkillCatalog, SkillInstallation
from opsmesh.agents.skills.preparation import EntryResolver
from opsmesh.agents.tools import ToolRegistry
from opsmesh.agents.tools.adapter import agent_as_tool, resolve_tool

ModelResolver = Callable[
    [ModelRef, AsyncExitStack], Awaitable[tuple[Model | str, ModelSettings | None]]
]


def _missing_entry(reference: EntryRef) -> BaseEntry:
    raise ValueError(f"Workspace entry requires an authorized resolver: {reference.entry_id}")


@dataclass
class AssemblyBindings:
    """A caller-authorized resource view. No user credentials or Sessions are cached here."""

    providers: ProviderRegistry = field(default_factory=ProviderRegistry)
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    skills: SkillCatalog = field(default_factory=SkillCatalog)
    callbacks: CallbackRegistry = field(default_factory=CallbackRegistry)
    credentials: CredentialResolver | None = None
    model_resolver: ModelResolver | None = None
    mcp_resolver: MCPResolver | None = None
    entry_resolver: EntryResolver = _missing_entry
    manifest_resolver: ManifestResolver | None = None


@dataclass(frozen=True)
class AgentBuild:
    """Objects live only within build_agent's context; borrowed MCP stays externally owned."""

    agent: Agent[Any]
    agents: Mapping[str, Agent[Any]]
    skill_installations: Mapping[str, tuple[SkillInstallation, ...]]


def _callback(value: object, kind: CallbackKind, registry: CallbackRegistry) -> Any:
    return registry.resolve(kind, value) if isinstance(value, CallbackRef) else value


def _agent_options(config: BaseAgentConfig, callbacks: CallbackRegistry) -> dict[str, Any]:
    mcp = config.mcp_config.model_dump(exclude_unset=True)
    if isinstance(config.mcp_config.failure_error_function, CallbackRef):
        mcp["failure_error_function"] = callbacks.resolve(
            "tool_error", config.mcp_config.failure_error_function
        )
    # Optional bool fields omitted from configuration preserve SDK defaults.
    for key in ("convert_schemas_to_strict", "include_server_in_tool_names"):
        if mcp.get(key) is None:
            mcp.pop(key, None)
    prompt: Any = config.prompt
    if isinstance(prompt, PromptConfig):
        prompt = prompt.model_dump(exclude_none=True)
    else:
        prompt = _callback(prompt, "prompt", callbacks)
    behavior: Any = config.tool_use_behavior
    if isinstance(behavior, StopAtTools):
        behavior = behavior.model_dump()
    else:
        behavior = _callback(behavior, "tool_use_behavior", callbacks)
    return {
        "name": config.name,
        "instructions": _callback(config.instructions, "instructions", callbacks),
        "handoff_description": config.handoff_description,
        "prompt": prompt,
        "mcp_config": mcp,
        "input_guardrails": [
            callbacks.resolve("input_guardrail", r) for r in config.input_guardrails
        ],
        "output_guardrails": [
            callbacks.resolve("output_guardrail", r) for r in config.output_guardrails
        ],
        "output_type": resolve_output(config.output_type, callbacks),
        "hooks": _callback(config.hooks, "agent_hooks", callbacks),
        "tool_use_behavior": behavior,
        "reset_tool_choice": config.reset_tool_choice,
    }


@asynccontextmanager
async def build_agent(
    agent_id: str,
    *,
    load: Callable[[str], AgentConfig | SandboxAgentConfig],
    bindings: AssemblyBindings,
    max_depth: int = 32,
    max_agents: int = 256,
) -> AsyncIterator[AgentBuild]:
    configs = resolve_agents(agent_id, load, max_depth=max_depth, max_agents=max_agents)
    agents: dict[str, Agent[Any]] = {}
    installations: dict[str, tuple[SkillInstallation, ...]] = {}
    async with AsyncExitStack() as lifespan:
        for identifier, config in configs.items():
            options = _agent_options(config, bindings.callbacks)
            settings: ModelSettings | None = None
            if config.model is not None:
                if bindings.model_resolver is not None:
                    model, settings = await bindings.model_resolver(config.model, lifespan)
                elif bindings.credentials is not None:
                    model, settings = await create_model(
                        config.model,
                        providers=bindings.providers,
                        credentials=bindings.credentials,
                        lifespan=lifespan,
                    )
                else:
                    raise ValueError("Model references require a model or credential resolver")
                if not isinstance(model, Model | str) or (isinstance(model, str) and not model):
                    raise TypeError("Model resolver must return a native SDK Model or model name")
                options["model"] = model
            if config.model_settings is not None:
                override = model_settings(config.model_settings)
                settings = override if settings is None else settings.resolve(override)
            if settings is not None:
                options["model_settings"] = settings
            options["mcp_servers"] = [
                await resolve_mcp(
                    reference,
                    resolver=bindings.mcp_resolver,
                    callbacks=bindings.callbacks,
                    lifespan=lifespan,
                )
                for reference in config.mcp_servers
            ]
            if isinstance(config, SandboxAgentConfig):
                options["default_manifest"] = resolve_manifest(
                    config.default_manifest,
                    entry_resolver=bindings.entry_resolver,
                    template_resolver=bindings.manifest_resolver,
                )
                options["base_instructions"] = _callback(
                    config.base_instructions, "instructions", bindings.callbacks
                )
                options["run_as"] = config.run_as
                if config.capabilities is not None:
                    prepared = resolve_capabilities(
                        config.capabilities,
                        skills=bindings.skills,
                        callbacks=bindings.callbacks,
                        entry_resolver=bindings.entry_resolver,
                    )
                    options["capabilities"] = prepared.capabilities
                    installations[identifier] = prepared.installations
                    manifest = options["default_manifest"]
                    if manifest is not None:
                        for capability in prepared.capabilities:
                            if isinstance(capability, Skills) and (
                                capability.from_ is not None or capability.lazy_from is not None
                            ):
                                # The SDK treats an existing directory as an installed
                                # from_ source. An empty layout placeholder must not
                                # suppress materialization or claim a lazy source root.
                                for path, entry in tuple(manifest.entries.items()):
                                    if Path(path).as_posix() != capability.skills_path:
                                        continue
                                    if isinstance(entry, Dir) and not entry.children:
                                        del manifest.entries[path]
                                    else:
                                        raise ValueError(
                                            "Skills source conflicts with Manifest entry"
                                        )
                agents[identifier] = SandboxAgent(**options)
            else:
                agents[identifier] = Agent(**options)
        # All objects exist before wiring references, preserving shared targets
        # within this build without sharing mutable Agent instances across builds.
        for identifier, config in configs.items():
            agent = agents[identifier]
            agent.tools = [
                agent_as_tool(binding, agents[binding.agent_id], bindings.callbacks)
                if isinstance(binding, AgentToolRef)
                else resolve_tool(binding, bindings.tools)
                for binding in config.tools
            ]
            for reference in config.handoffs:
                options = reference.model_dump(exclude={"agent_id"}, exclude_none=True)
                callback_fields: dict[str, CallbackKind] = {
                    "on_handoff": "on_handoff",
                    "input_type": "input_type",
                    "input_filter": "handoff_filter",
                    "is_enabled": "tool_enabled",
                }
                for name, kind in callback_fields.items():
                    value = getattr(reference, name)
                    if isinstance(value, CallbackRef):
                        options[name] = bindings.callbacks.resolve(kind, value)
                agent.handoffs.append(handoff(agents[reference.agent_id], **options))
            names = [tool.name for tool in agent.tools]
            names.extend(
                item.tool_name if isinstance(item, Handoff) else Handoff.default_tool_name(item)
                for item in agent.handoffs
            )
            if len(names) != len(set(names)):
                raise ValueError(f"Agent contains duplicate tool/handoff names: {identifier}")
        yield AgentBuild(agents[agent_id], agents, installations)
