"""Build a fresh, version-fingerprinted SDK graph for one platform run."""

import asyncio
import hashlib
import inspect
import json
from collections.abc import Callable
from typing import Any

from agents import Agent
from agents.prompts import Prompt
from agents.sandbox import Manifest, SandboxAgent
from openai import AsyncOpenAI
from pydantic import TypeAdapter

from opsmesh.framework.agents import AgentExecutionContext, AgentSpecification, ToolBinding
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import RuntimeExtensions

from .execution_state import execution_state
from .extension_mapping import output_type, tool_completion
from .guardrails import build_guardrail
from .hooks import RegisteredAgentHooks, resolve_hooks
from .hosted_tools import build_hosted_tools
from .mcp_config import configure_mcp_tools
from .models import resolve_profile_model, resolve_settings
from .native_tools import build_native_tools
from .sandbox import prepare_capabilities
from .sandbox_delegation import isolate_agent_tool, restoration_tools
from .tool_selection import resolve_tool_selectors
from .tools import build_agent_tool, build_function_tool, build_handoff


class AgentGraphBuilder:
    def __init__(
        self,
        extensions: RuntimeExtensions,
        context: AgentExecutionContext,
        config_factory: Callable[[AgentSpecification], Any],
        model_resolver: Any = None,
    ) -> None:
        self.extensions = extensions
        self.context = context
        self.config_factory = config_factory
        self.model_resolver = model_resolver
        self.cache: dict[str, Agent[Any]] = {}
        self.templates: dict[str, Agent[Any]] = {}
        self.graph: dict[str, str] = {}
        self.resolved_templates: dict[str, dict[str, Any]] = {}
        self.clients: list[AsyncOpenAI] = []
        self.transports: list[Any] = []
        self.model_clients: dict[str, Any] = {}
        self.delegated_tasks: set[asyncio.Task[Any]] = set()

    async def close(self) -> None:
        pending = [task for task in self.delegated_tasks if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for model in self.transports:
            await model.close()
        for client in self.clients:
            await client.close()

    async def _instructions(self, spec: AgentSpecification) -> tuple[Any, tuple[Any, ...]]:
        extra = ""
        tools: tuple[ToolBinding, ...] = ()
        prepare = self.context.services.prepare_agent_resources
        if prepare is not None:
            resources = await prepare(spec)
            extra, tools = resources.instructions, configure_mcp_tools(spec, resources.tools)
        if spec.instruction_ref:
            callback = self.extensions.instruction(spec.instruction_ref)

            async def dynamic(wrapper: Any, agent: Any) -> str:
                value = callback(wrapper.context, spec)
                value = await value if inspect.isawaitable(value) else value
                return "\n\n".join(part for part in (value, extra) if part)

            return dynamic, tools
        return "\n\n".join(part for part in (spec.instructions, extra) if part), tools

    async def _options(self, spec: AgentSpecification) -> tuple[dict[str, Any], tuple[Any, ...]]:
        instructions, resource_tools = await self._instructions(spec)
        selected = resolve_tool_selectors(spec, resource_tools)
        if self.model_resolver is not None:
            model, profile = self.model_resolver(spec, self.context), None
        else:
            model, profile = await resolve_profile_model(spec, self.context, self.clients)
            self.model_clients[spec.ref] = self.clients[-1]
            if spec.model_transport == "websocket":
                self.transports.append(model)
        if (
            spec.hosted_tools
            and profile is not None
            and profile.get("api", "responses") != "responses"
        ):
            raise OpsMeshError(
                "FEATURE_UNSUPPORTED", "Provider-hosted tools require a Responses model profile"
            )
        return {
            "name": f"{spec.name} [{spec.ref}]",
            "instructions": instructions,
            "model": model,
            "model_settings": resolve_settings(selected, profile),
            "prompt": self._prompt(spec),
            "reset_tool_choice": spec.reset_tool_choice,
            "tool_use_behavior": tool_completion(selected, self.extensions),
            "output_type": output_type(spec, self.extensions),
            "input_guardrails": [
                build_guardrail(self.extensions, g) for g in spec.guardrails if g.stage == "input"
            ],
            "output_guardrails": [
                build_guardrail(self.extensions, g) for g in spec.guardrails if g.stage == "output"
            ],
            "hooks": RegisteredAgentHooks(resolve_hooks(spec, self.extensions, "agent")),
        }, resource_tools

    def _prompt(self, spec: AgentSpecification) -> Any:
        if spec.prompt_template_ref:
            template = self.extensions.prompt_template(spec.prompt_template_ref)
            variables = {**template.defaults, **spec.prompt_variables}
            missing = set(template.required_variables) - set(variables)
            unknown = (
                set(variables) - set(template.allowed_variables)
                if template.allowed_variables is not None
                else set()
            )
            if missing or unknown:
                raise OpsMeshError(
                    "PROMPT_VARIABLES_INVALID",
                    "Template variables do not match its declaration",
                    details={"missing": sorted(missing), "unknown": sorted(unknown)},
                )
            self.resolved_templates[spec.ref] = template.model_dump(mode="json")
            return TypeAdapter(Prompt).validate_python(
                {
                    "id": template.remote_id,
                    "version": template.remote_version,
                    "variables": variables,
                }
            )
        if spec.prompt_ref is None:
            return spec.prompt
        callback = self.extensions.prompt(spec.prompt_ref)

        async def dynamic(data: Any) -> dict[str, Any]:
            value = callback(data.context.context, spec)
            return await value if inspect.isawaitable(value) else value

        return dynamic

    async def _resolve_child(
        self, ref: str, parent: AgentSpecification, *, template: bool = False
    ) -> tuple[AgentSpecification, Agent[Any]]:
        if self.context.resolve_agent is None:
            raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "No agent resolver configured")
        child = await self.context.resolve_agent(ref)
        if child.backend != parent.backend:
            raise OpsMeshError("FEATURE_UNSUPPORTED", "Cross-backend native delegation unsupported")
        if (
            child.sandbox is not None
            and not template
            and self.context.resources.sandbox_session is not None
        ):
            root = self.context.resources.sandbox_root_specification
            if (
                root is None
                or child.sandbox != root.sandbox
                or child.workspace_id != root.workspace_id
            ):
                raise OpsMeshError(
                    "FEATURE_UNSUPPORTED", "Delegated sandbox requires a separate resource policy"
                )
        return child, await self.build(child, template=template)

    async def _delegation(
        self, agent: Agent[Any], spec: AgentSpecification, *, template: bool = False
    ) -> None:
        for handoff_binding in spec.handoffs:
            _, target = await self._resolve_child(
                handoff_binding.agent_ref, spec, template=template
            )
            agent.handoffs.append(build_handoff(handoff_binding, target, self.extensions))
        for tool_binding in spec.agent_tools:
            if self.context.resolve_agent is None:
                raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "No agent resolver configured")
            proposed = await self.context.resolve_agent(tool_binding.agent_ref)
            root = self.context.resources.sandbox_root_specification
            different = (
                root is None
                or proposed.sandbox != root.sandbox
                or proposed.workspace_id != root.workspace_id
            )
            isolate = proposed.sandbox is not None and (
                tool_binding.sandbox_policy == "isolated"
                or (tool_binding.sandbox_policy == "auto" and different)
            )
            if isolate:
                if proposed.backend != spec.backend:
                    raise OpsMeshError(
                        "FEATURE_UNSUPPORTED", "Cross-backend native delegation unsupported"
                    )
                target = await self.build(proposed, template=True)
                tool = build_agent_tool(tool_binding, target, proposed, None, self.extensions, spec)
                agent.tools.append(
                    isolate_agent_tool(
                        tool,
                        tool_binding,
                        proposed,
                        spec,
                        self.extensions,
                        self.model_resolver,
                        self.delegated_tasks,
                    )
                )
                continue
            child, target = await self._resolve_child(
                tool_binding.agent_ref, spec, template=template
            )
            agent.tools.append(
                build_agent_tool(
                    tool_binding, target, child, self.config_factory(child), self.extensions, spec
                )
            )

    async def build(self, spec: AgentSpecification, *, template: bool = False) -> Agent[Any]:
        cache = self.templates if template else self.cache
        if spec.ref in cache:
            return cache[spec.ref]
        options, resource_tools = await self._options(spec)
        fingerprint = {
            "agent": spec.model_dump(mode="json"),
            "prepared_tools": [tool.model_dump(mode="json") for tool in resource_tools],
            "prompt_template": self.resolved_templates.get(spec.ref),
        }
        self.graph[spec.ref] = hashlib.sha256(
            json.dumps(fingerprint, sort_keys=True).encode()
        ).hexdigest()
        if spec.sandbox and not template:
            options.update(
                default_manifest=Manifest.model_validate(spec.sandbox.manifest),
                base_instructions=spec.sandbox.base_instructions,
                run_as=spec.sandbox.run_as,
                capabilities=await prepare_capabilities(
                    spec, self.context, self.clients, self.model_resolver
                ),
            )
            agent: Agent[Any] = SandboxAgent(**options)
        else:
            agent = Agent(**options)
        cache[spec.ref] = agent  # Register before recursion to support handoff cycles.
        if template and spec.sandbox:
            agent.tools.extend(restoration_tools(spec))
        execution_state(self.context).agent_model_profiles[agent.name] = spec.model_profile_ref
        bindings = (*spec.tools, *resource_tools)
        if len({t.name for t in bindings}) != len(bindings):
            raise OpsMeshError("CONFIG_INVALID", "Prepared resource tool names collide")
        agent.tools.extend(build_function_tool(t, spec, self.extensions) for t in bindings)
        agent.tools.extend(await build_hosted_tools(spec, self.context, self.extensions))
        agent.tools.extend(build_native_tools(spec, self.extensions))
        await self._delegation(agent, spec, template=template)
        return agent
