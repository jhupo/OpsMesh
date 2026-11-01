"""Validate publication references against deployed code and persistent catalogs."""

from __future__ import annotations

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.agents.api import AgentDefinitionCatalog
from opsmesh.modules.capabilities.api import (
    CapabilityCatalog,
    CodeToolRegistry,
    MCPServerCatalog,
    ScriptTools,
    SkillCatalog,
)
from opsmesh.modules.models.api import ModelProfiles
from opsmesh.modules.models.credentials import ModelCredentialManagement
from opsmesh.modules.runtime.api import RuntimeExtensions

from .authorization import PlatformAuthorization
from .ports import ApplicationSettings


class AgentPublicationValidator:
    def __init__(
        self,
        *,
        agents: AgentDefinitionCatalog,
        capabilities: CapabilityCatalog,
        code_tools: CodeToolRegistry,
        extensions: RuntimeExtensions,
        mcp_catalog: MCPServerCatalog,
        models: ModelProfiles,
        authorization: PlatformAuthorization,
        model_credentials: ModelCredentialManagement,
        scripts: ScriptTools,
        settings: ApplicationSettings,
        skills: SkillCatalog,
    ) -> None:
        self.agents = agents
        self.capabilities = capabilities
        self.code_tools = code_tools
        self.extensions = extensions
        self.mcp_catalog = mcp_catalog
        self.models = models
        self.authorization = authorization
        self.model_credentials = model_credentials
        self.scripts = scripts
        self.settings = settings
        self.skills = skills

    async def validate(self, specification: AgentSpecification) -> None:
        self.extensions.backend(specification.backend)
        if specification.backend == "openai_agents" and not specification.model_profile_ref:
            raise OpsMeshError(
                "CONFIG_INVALID", "Published OpenAI agents need a model profile revision"
            )
        if specification.model_profile_ref:
            if "@" not in specification.model_profile_ref:
                raise OpsMeshError(
                    "CONFIG_INVALID", "Model profiles must reference an immutable revision"
                )
            required = {"text"}
            if (
                specification.tools
                or specification.mcp_refs
                or specification.agent_tools
                or specification.skill_refs
                or specification.knowledge_collections
                or specification.handoffs
                or specification.sandbox
                or specification.hosted_tools
                or specification.native_tools
            ):
                required.add("tools")
            if specification.output_schema or specification.output_type_ref:
                required.add("structured_output")
            await self.models.require_features(specification.model_profile_ref, required)
        for binding in specification.tools:
            tool = await self.capabilities.get_tool(binding.tool_ref or binding.name)
            if tool.code_ref.startswith("script:"):
                await self.scripts.get(tool.code_ref.removeprefix("script:"))
            else:
                self.code_tools.resolve(tool.code_ref)
            if binding.input_schema != tool.input_schema:
                raise OpsMeshError(
                    "CONFIG_INVALID", "Bound tool schema differs from its catalog version"
                )
        for reference in specification.skill_refs:
            await self.skills.get(reference)
        for reference in specification.mcp_refs:
            await self.mcp_catalog.get(reference)
        for reference in [item.agent_ref for item in specification.handoffs] + [
            item.agent_ref for item in specification.agent_tools
        ]:
            if "@" not in reference:
                raise OpsMeshError(
                    "CONFIG_INVALID", "Delegated agents require a published revision"
                )
            await self.agents.get_revision(reference)
        for guard in specification.guardrails:
            self.extensions.guardrail(guard.ref)
        for hook in specification.hooks:
            self.extensions.hook(hook.ref)
        if specification.instruction_ref:
            self.extensions.instruction(specification.instruction_ref)
        self._validate_extensions(specification)
        if (
            specification.sandbox
            and specification.sandbox.provider == "local"
            and not self.settings.allow_trusted_local
        ):
            raise OpsMeshError("FORBIDDEN", "Trusted local execution is disabled")

    def _validate_extensions(self, spec: AgentSpecification) -> None:
        extensions = self.extensions
        if spec.prompt_ref:
            extensions.prompt(spec.prompt_ref)
        if spec.output_type_ref:
            extensions.output_type(spec.output_type_ref)
        if spec.tool_completion_ref:
            extensions.tool_completion(spec.tool_completion_ref)
        for native in spec.native_tools:
            if native.type == "shell":
                extensions.shell(native.executor_ref)
            elif native.type == "apply_patch":
                extensions.patch(native.editor_ref)
            else:
                extensions.computer(native.computer_ref)
                if native.safety_check_ref:
                    extensions.computer_safety(native.safety_check_ref)
        for hosted in spec.hosted_tools:
            if hosted.type == "mcp" and hosted.approval_policy_ref:
                extensions.mcp_approval(hosted.approval_policy_ref)
        for reference in (
            [item.enabled_ref for item in spec.tools]
            + [item.enabled_ref for item in spec.handoffs]
            + [item.enabled_ref for item in spec.agent_tools]
        ):
            if reference:
                extensions.visibility(reference)
        for handoff in spec.handoffs:
            if handoff.input_filter_ref:
                extensions.handoff_filter(handoff.input_filter_ref)
            if handoff.callback_ref:
                extensions.hook(handoff.callback_ref)
        for tool in spec.agent_tools:
            if tool.output_extractor_ref:
                extensions.output_extractor(tool.output_extractor_ref)
            if tool.input_type_ref:
                extensions.output_type(tool.input_type_ref)
            if tool.input_builder_ref:
                extensions.agent_input(tool.input_builder_ref)

    async def prepare(self, specification: AgentSpecification, owner_id: str) -> AgentSpecification:
        if specification.model_profile_ref is None and specification.backend == "openai_agents":
            reference = await self.models.default_reference(owner_id)
            if reference is None:
                raise OpsMeshError(
                    "CONFIG_INVALID", "Configure a default model or bind one explicitly"
                )
            await self.authorization.check(owner_id, "model:use", reference)
            specification = specification.model_copy(update={"model_profile_ref": reference})
        if specification.model_profile_ref:
            profile = await self.models.get(specification.model_profile_ref)
            if profile.owner_id:
                await self.authorization.check(
                    owner_id, "model:use", specification.model_profile_ref
                )
                await self.model_credentials.validate_reference(
                    profile.secret_ref, actor_id=owner_id
                )
        return specification
