"""Caller-owned user binding transactions and current reference checks."""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.lifecycle import Viewer, authorize_version_read
from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.extensions.access import load_installation
from opsmesh.extensions.models import ExtensionInstance
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.users.ownership import require_resource_owner
from opsmesh.orchestration.configurations.bindings.access import load_binding
from opsmesh.orchestration.configurations.bindings.models import CapabilityBinding
from opsmesh.orchestration.configurations.bindings.schemas import (
    BindingConfiguration,
    BindingCreate,
    BindingUpdate,
)
from opsmesh.orchestration.configurations.bindings.validation import render, validate_parameters
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.security.redaction import redaction_rules_for
from opsmesh.shared.serialization import canonical_json


def validate_configuration(
    session: Session,
    *,
    actor: CurrentUser,
    owner_user_id: UUID,
    version_id: UUID,
    configuration: BindingConfiguration,
) -> CapabilityVersion:
    require_resource_owner(session, owner_user_id=owner_user_id, user_id=actor.user_id)
    version = session.get(CapabilityVersion, version_id)
    if version is None or version.status != "published" or not version.digest:
        raise ForbiddenError("A binding requires an immutable published capability")
    definition = authorize_version_read(
        session,
        version=version,
        viewer=Viewer(user_id=actor.user_id, is_platform_admin=False),
    )
    if definition.status != "active":
        raise ForbiddenError("The capability is not active")
    content = cast(dict[str, Any], version.configuration)
    validate_parameters(
        dict(content.get("parameters_schema", {})),
        configuration.parameters,
        rules=redaction_rules_for(session),
    )
    render(
        {key: value for key, value in content.items() if key != "parameters_schema"},
        configuration.parameters,
    )
    if configuration.instance_id is not None:
        instance = session.get(ExtensionInstance, configuration.instance_id)
        if instance is None or instance.owner_id != owner_user_id or instance.status != "enabled":
            raise ForbiddenError("The extension instance is outside the binding scope")
        installation = load_installation(session, instance.installation_id)
        if installation.version_id != version.id or installation.status != "enabled":
            raise ForbiddenError("The installation does not match the capability version")
    return version


def create_binding(
    session: Session, *, actor: CurrentUser, owner_user_id: UUID, request: BindingCreate
) -> CapabilityBinding:
    require_resource_owner(session, owner_user_id=owner_user_id, user_id=actor.user_id, lock=True)
    version = validate_configuration(
        session,
        actor=actor,
        owner_user_id=owner_user_id,
        version_id=request.version_id,
        configuration=request.configuration,
    )
    if (
        session.scalar(
            select(CapabilityBinding.id).where(
                CapabilityBinding.owner_user_id == owner_user_id,
                CapabilityBinding.name == request.name,
            )
        )
        is not None
    ):
        raise ConflictError("A binding with this name already exists")
    record = CapabilityBinding(
        owner_user_id=owner_user_id,
        name=request.name,
        version_id=version.id,
        version_digest=version.digest,
        configuration=request.configuration.model_dump(mode="json"),
        created_by=actor.user_id,
    )
    session.add(record)
    session.flush()
    audit_binding(session, actor, record)
    return record


def update_binding(
    session: Session,
    *,
    actor: CurrentUser,
    owner_user_id: UUID,
    binding_id: UUID,
    request: BindingUpdate,
) -> CapabilityBinding:
    require_resource_owner(session, owner_user_id=owner_user_id, user_id=actor.user_id, lock=True)
    record, _, _ = load_binding(
        session, actor=actor, owner_user_id=owner_user_id, binding_id=binding_id, lock=True
    )
    if record.revision != request.expected_revision:
        raise ConflictError("The binding revision changed")
    version = validate_configuration(
        session,
        actor=actor,
        owner_user_id=owner_user_id,
        version_id=request.version_id,
        configuration=request.configuration,
    )
    record.version_id, record.version_digest = version.id, version.digest or ""
    record.configuration = request.configuration.model_dump(mode="json")
    record.status, record.revision = request.status, record.revision + 1
    audit_binding(session, actor, record)
    return record


def audit_binding(session: Session, actor: CurrentUser, record: CapabilityBinding) -> None:
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action="capability_binding.configured",
            actor=AuditActor(actor_type="user", actor_id=str(actor.user_id)),
            source="platform",
            target_type="capability_binding",
            target_id=str(record.id),
            summary="User capability binding configured",
            payload_json=canonical_json({"revision": record.revision}),
        ),
    )
