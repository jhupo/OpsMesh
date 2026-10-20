"""Recompute fixed MCP declarations and delegate connection lifecycle to runtime."""

from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser
from opsmesh.identity.users.ownership import require_resource_owner
from opsmesh.orchestration.configurations.bindings.access import load_binding
from opsmesh.orchestration.configurations.bindings.models import CapabilityBinding
from opsmesh.orchestration.configurations.bindings.schemas import (
    BindingConfiguration,
    BindingConnect,
)
from opsmesh.orchestration.configurations.bindings.service import (
    audit_binding,
    validate_configuration,
)
from opsmesh.orchestration.configurations.bindings.validation import render, validate_parameters
from opsmesh.orchestration.contracts import ExecutionSubject, ScopeAdmission
from opsmesh.orchestration.runtime import mcp_service
from opsmesh.orchestration.runtime.models import MCPConnection
from opsmesh.orchestration.runtime.schemas import MCPCreateRequest
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError
from opsmesh.shared.security.redaction import RedactionRules, redaction_rules_for


def compile_template(
    *,
    rules: RedactionRules,
    content: dict[str, Any],
    parameters: dict[str, Any],
    configuration_id: UUID | None,
    name: str,
    environment_id: UUID | None,
    transport: Literal["streamable_http", "sse", "stdio"] = "streamable_http",
) -> MCPCreateRequest:
    validate_parameters(content.get("parameters_schema", {}), parameters, rules=rules)
    effective = render(
        {
            key: content[key]
            for key in ("transport", "endpoint", "command", "arguments", "environment")
            if key in content
        },
        parameters,
    )
    values = effective.get("environment", {})
    local = effective["transport"] == "stdio"
    if not local and transport == "stdio":
        raise InvalidArgumentError("A remote template cannot use the stdio transport")
    if not local and values:
        raise InvalidArgumentError("Remote MCP does not accept environment parameters")
    try:
        return MCPCreateRequest(
            configuration_id=configuration_id,
            name=name,
            transport="stdio" if local else transport,
            endpoint=effective.get("endpoint"),
            environment_id=environment_id if local else None,
            command=[*(effective.get("command") or []), *effective.get("arguments", [])]
            if local
            else [],
            environment=values,
        )
    except ValidationError as exc:
        raise InvalidArgumentError(
            "Materialized MCP parameters violate the connection contract"
        ) from exc


def validate_connection(connection: MCPConnection, expected: MCPCreateRequest) -> None:
    if (
        connection.transport != expected.transport
        or connection.endpoint != expected.endpoint
        or connection.environment_id != expected.environment_id
        or connection.configuration.get("command", []) != expected.command
        or connection.configuration.get("environment", {}) != expected.environment
    ):
        raise ForbiddenError("The MCP connection differs from its fixed declaration and parameters")


def connect_binding(
    session: Session,
    *,
    actor: CurrentUser,
    owner_user_id: UUID,
    binding_id: UUID,
    request: BindingConnect,
    operation_id: str,
    admission: ScopeAdmission,
) -> CapabilityBinding:
    require_resource_owner(session, owner_user_id=owner_user_id, user_id=actor.user_id, lock=True)
    binding, version, kind = load_binding(
        session,
        actor=actor,
        owner_user_id=owner_user_id,
        binding_id=binding_id,
        use=True,
        lock=True,
    )
    if kind != "mcp":
        raise InvalidArgumentError("Only an MCP template can create an MCP connection")

    def create() -> CapabilityBinding:
        if binding.revision != request.expected_revision:
            raise ConflictError("The binding revision changed")
        settings = BindingConfiguration.model_validate(binding.configuration)
        validate_configuration(
            session,
            actor=actor,
            owner_user_id=owner_user_id,
            version_id=version.id,
            configuration=settings,
        )
        compiled = compile_template(
            content=version.configuration,
            parameters=settings.parameters,
            configuration_id=request.configuration_id,
            name=binding.name,
            environment_id=request.environment_id,
            transport=request.transport,
            rules=redaction_rules_for(session),
        )
        connection = mcp_service.create(
            session,
            subject=ExecutionSubject(actor.user_id),
            admission=admission,
            request=compiled,
            operation_id=f"binding:{binding.id}:{operation_id}",
        )
        binding.configuration = settings.model_copy(
            update={"mcp_connection_id": connection.id}
        ).model_dump(mode="json")
        binding.revision += 1
        audit_binding(session, actor, binding)
        return binding

    return run_idempotent_create(
        session=session,
        scope_id=owner_user_id,
        actor_id=actor.user_id,
        operation=f"binding.connect:{binding_id}",
        idempotency_key=operation_id,
        request_payload=request.model_dump(mode="json"),
        get_existing=lambda _: binding,
        create=create,
        resource_id=lambda value: value.id,
    )
