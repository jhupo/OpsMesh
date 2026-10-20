"""Composition-owned state and explicit execution operations."""

from typing import Any, Literal, cast
from uuid import UUID

from opsmesh_sdk.manifest import PackageManifest
from sqlalchemy.orm import Session

from opsmesh.extensions.access import load_installation
from opsmesh.extensions.models import ExtensionInstance
from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.configurations.bindings.access import load_binding
from opsmesh.orchestration.configurations.bindings.connections import (
    compile_template,
    validate_connection,
)
from opsmesh.orchestration.configurations.bindings.schemas import BindingConfiguration
from opsmesh.orchestration.configurations.bindings.service import validate_configuration
from opsmesh.orchestration.configurations.bindings.validation import render, validate_parameters
from opsmesh.orchestration.configurations.contracts import ModelSelectionAuthorization
from opsmesh.orchestration.configurations.models import (
    ConfigurationRevision,
    UseConfiguration,
)
from opsmesh.orchestration.configurations.schemas import ConfigurationSpec
from opsmesh.orchestration.contracts import (
    ExecutionScope,
    ExecutionSubject,
    ScopeAdmission,
)
from opsmesh.orchestration.definitions.schemas import AgentGraphSpec
from opsmesh.orchestration.definitions.service import load_revision
from opsmesh.orchestration.execution.contracts import (
    AgentConfiguration,
    ExecutionRequest,
    InstalledExport,
    InstalledHook,
    MCPBinding,
    ToolConfiguration,
)
from opsmesh.orchestration.runtime.access import environment_scope, load_environment
from opsmesh.orchestration.runtime.mcp import connection_scope, load_connection
from opsmesh.shared.errors import ForbiddenError, InvalidArgumentError
from opsmesh.shared.security.redaction import redaction_rules_for
from opsmesh.shared.serialization import payload_digest


def compile_configuration(
    session: Session,
    *,
    actor: CurrentUser,
    configuration: UseConfiguration,
    revision: ConfigurationRevision,
    session_id: UUID,
    input: Any,
    admission: ScopeAdmission,
) -> tuple[ExecutionRequest, dict[str, Any]]:
    if revision.configuration_id != configuration.id:
        raise ForbiddenError("The configuration revision belongs to another configuration")
    owner = actor
    definition_revision, definition = load_revision(
        session, actor=owner, revision_id=configuration.definition_revision_id, use=True
    )
    if definition.kind != "agent_graph":
        raise InvalidArgumentError("Only Agent graphs submit directly to execution")
    graph = AgentGraphSpec.model_validate(definition_revision.spec)
    settings = ConfigurationSpec.model_validate(revision.specification)
    frozen: dict[str, Any] = {
        "definition_digest": definition_revision.digest,
        "root_directory_id": str(configuration.root_directory_id),
    }
    subject = ExecutionSubject(owner.user_id)
    scope = ExecutionScope(configuration_id=configuration.id)
    if settings.environment_id:
        environment = load_environment(
            session, environment_id=settings.environment_id, subject=subject, admission=admission
        )
        if environment_scope(environment) != scope or environment.status != "ready":
            raise ForbiddenError("The explicitly selected environment is unavailable")
        frozen["environment_id"] = str(environment.id)
    for model in settings.models.values():
        if not isinstance(admission, ModelSelectionAuthorization):
            raise ForbiddenError("Model selection authorization is not assembled")
        number = admission.authorize_model(
            session, user_id=owner.user_id, connection_id=model.connection_id, model=model.name
        )
        frozen[f"model:{model.connection_id}"] = {"revision": number}
    tools: dict[str, ToolConfiguration] = {}
    mcps: dict[str, MCPBinding] = {}
    skills: dict[str, str] = {}
    for name, selection in settings.capabilities.items():
        binding, version, kind = load_binding(
            session,
            actor=owner,
            owner_user_id=owner.user_id,
            binding_id=selection.binding_id,
            use=True,
        )
        binding_settings = BindingConfiguration.model_validate(binding.configuration)
        validate_configuration(
            session,
            actor=owner,
            owner_user_id=owner.user_id,
            version_id=version.id,
            configuration=binding_settings,
        )
        parameters = binding_settings.parameters | selection.parameters
        parameter_schema = version.configuration.get("parameters_schema", {})
        if not isinstance(parameter_schema, dict):
            raise InvalidArgumentError("The published capability parameter schema is invalid")
        validate_parameters(parameter_schema, parameters, rules=redaction_rules_for(session))
        content = render(version.configuration, parameters)
        frozen[str(binding.id)] = {
            "revision": binding.revision,
            "version_id": str(version.id),
            "digest": version.digest,
        }
        if kind in {"tool", "extension"}:
            instance = (
                session.get(ExtensionInstance, binding_settings.instance_id)
                if (binding_settings.instance_id is not None)
                else None
            )
            if (
                instance is None
                or instance.owner_id != owner.user_id
                or (instance.configuration_id != configuration.id or instance.status != "enabled")
            ):
                raise ForbiddenError("The installed export is outside the configuration")
            installation = load_installation(session, instance.installation_id)
            if installation.status != "enabled" or installation.version_id != version.id:
                raise ForbiddenError("The installed capability was revoked or replaced")
            manifest = PackageManifest.model_validate(installation.manifest)
            export = next(
                (
                    item
                    for item in manifest.operations
                    if item.export_id == binding_settings.export_id
                ),
                None,
            )
            if export is None or not set(export.permissions) <= (
                set(installation.permission_ceiling) & set(instance.permissions)
            ):
                raise ForbiddenError("The installed operation permissions were revoked")
            frozen[str(binding.id)]["instance_revision"] = instance.revision
            tools[name] = ToolConfiguration(
                name=name,
                description=export.export_id,
                input_schema=export.input_schema.model_dump_schema(),
                extension=InstalledExport(
                    instance_id=instance.id,
                    installation_id=installation.id,
                    configuration_revision=instance.revision,
                    artifact=installation.artifact or "",
                    export_id=export.export_id,
                ),
                approval_required=bool(set(export.effects) - {"none"}),
            )
        elif kind == "mcp":
            if binding_settings.mcp_connection_id is None:
                raise InvalidArgumentError("MCP slots require an explicitly connected server")
            connection_record = load_connection(
                session,
                connection_id=binding_settings.mcp_connection_id,
                principal_id=owner.user_id,
                admission=admission,
            )
            if connection_scope(connection_record) != scope or connection_record.status != "ready":
                raise ForbiddenError("The MCP connection is unavailable in this configuration")
            if connection_record.environment_id is not None:
                mcp_environment = load_environment(
                    session,
                    environment_id=connection_record.environment_id,
                    subject=subject,
                    admission=admission,
                )
                if environment_scope(mcp_environment) != scope or mcp_environment.status != "ready":
                    raise ForbiddenError("The MCP environment is unavailable in this configuration")
                frozen[str(binding.id)]["environment_id"] = str(mcp_environment.id)
            expected = compile_template(
                content=version.configuration,
                parameters=parameters,
                configuration_id=configuration.id,
                name=binding.name,
                environment_id=connection_record.environment_id,
                transport=cast(
                    Literal["streamable_http", "sse", "stdio"], connection_record.transport
                ),
                rules=redaction_rules_for(session),
            )
            validate_connection(connection_record, expected)
            declared = {item["name"] for item in content.get("tools", [])}
            discovered = {item["name"]: item for item in connection_record.tools}
            if not declared or not declared <= discovered.keys():
                raise ForbiddenError("The MCP tools differ from the published capability")
            for item in content.get("tools", []):
                if item.get("parameters_schema") and item["parameters_schema"] != (
                    discovered[item["name"]].get("inputSchema")
                ):
                    raise ForbiddenError("The MCP tool schema changed")
            mcps[name] = MCPBinding(
                connection_id=connection_record.id,
                tools_digest=payload_digest(
                    {"tools": sorted(connection_record.tools, key=lambda item: str(item["name"]))}
                ),
                allowed_tools=sorted(declared),
            )
        elif kind == "skill":
            skills[name] = str(content["instructions"])
        else:
            raise InvalidArgumentError("The capability cannot be attached to an Agent")
    hooks = []
    for hook_selection in settings.hooks:
        binding, version, kind = load_binding(
            session,
            actor=owner,
            owner_user_id=owner.user_id,
            binding_id=hook_selection.binding_id,
            use=True,
        )
        binding_settings = BindingConfiguration.model_validate(binding.configuration)
        validate_configuration(
            session,
            actor=owner,
            owner_user_id=owner.user_id,
            version_id=version.id,
            configuration=binding_settings,
        )
        instance = (
            session.get(ExtensionInstance, binding_settings.instance_id)
            if binding_settings.instance_id is not None
            else None
        )
        if (
            kind not in {"tool", "extension"}
            or instance is None
            or instance.owner_id != owner.user_id
            or instance.configuration_id != configuration.id
            or instance.status != "enabled"
        ):
            raise ForbiddenError("The installed hook is outside the configuration")
        installation = load_installation(session, instance.installation_id)
        manifest = PackageManifest.model_validate(installation.manifest)
        hook = next(
            (item for item in manifest.hooks if item.export_id == binding_settings.export_id), None
        )
        if (
            installation.status != "enabled"
            or installation.version_id != version.id
            or hook is None
            or not set(hook_selection.stages) <= set(hook.stages)
            or not set(hook.permissions)
            <= (set(installation.permission_ceiling) & set(instance.permissions))
        ):
            raise ForbiddenError("The installed hook declaration or permissions changed")
        frozen[str(binding.id)] = {
            "revision": binding.revision,
            "version_id": str(version.id),
            "digest": version.digest,
            "instance_revision": instance.revision,
        }
        hooks.append(
            InstalledHook(
                instance_id=instance.id,
                installation_id=installation.id,
                configuration_revision=instance.revision,
                artifact=installation.artifact or "",
                export_id=hook.export_id,
                binding_id=str(binding.id),
                order=hook_selection.order,
                stages=hook_selection.stages,
                failure_policy=hook.failure_policy,
                budget=hook_selection.budget,
            )
        )
    agents: dict[str, AgentConfiguration] = {}
    for key, agent in graph.agents.items():
        if (
            agent.model_slot not in settings.models
            or any(name not in tools for name in agent.tools)
            or (
                any(name not in mcps for name in agent.mcp)
                or any(name not in skills for name in agent.skills)
            )
        ):
            raise InvalidArgumentError("An Agent has an unbound model or capability slot")
        agents[key] = AgentConfiguration(
            name=agent.name,
            instructions=agent.instructions + "".join("\n" + skills[name] for name in agent.skills),
            model=settings.models[agent.model_slot],
            tools=[tools[name] for name in agent.tools],
            mcp_connections=[mcps[name] for name in agent.mcp],
            handoffs=agent.handoffs,
            agent_tools=agent.agent_tools,
            output_schema=agent.output_schema,
        )
    return ExecutionRequest(
        session_id=session_id,
        input=input,
        entry_agent=graph.entry_agent,
        agents=agents,
        hooks=hooks,
        limits=graph.limits,
        environment_id=settings.environment_id,
        assets=settings.assets,
        outputs=settings.outputs,
    ), frozen
