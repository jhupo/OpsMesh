"""Administrator installation lifecycle and owner-controlled instance revisions."""

import json
import tomllib
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError
from opsmesh_sdk.manifest import HookExport, OperationExport, PackageManifest
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.lifecycle import Viewer, get_definition, read_version_package
from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.capabilities.packages import read_archive
from opsmesh.extensions.access import (
    ScopeAuthorization,
    authorize_installation,
    authorize_instance,
    load_installation,
    require_admin,
)
from opsmesh.extensions.contracts import InstanceReferences
from opsmesh.extensions.models import ExtensionInstance, Installation, Invocation, ManagedDeployment
from opsmesh.extensions.schemas import InstallRequest, InstanceConfiguration, extension_work
from opsmesh.identity.context import CurrentUser
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.shared.security.redaction import redact_sensitive_payload, redaction_rules_for
from opsmesh.shared.serialization import canonical_json
from opsmesh.temporal.dispatch.outbox import stage_execution, stage_signal


def request_installation(
    session: Session,
    *,
    user: CurrentUser,
    request: InstallRequest,
    storage_root: Path,
) -> Installation:
    require_admin(user)

    def create() -> Installation:
        version = session.get(CapabilityVersion, request.version_id)
        if version is None or version.status != "published":
            raise InvalidArgumentError("Installation requires a published capability version")
        definition = get_definition(
            session,
            definition_id=version.definition_id,
            viewer=Viewer(user_id=user.user_id, is_platform_admin=True),
        )
        if definition.type not in {"extension", "tool"} or definition.status != "active":
            raise InvalidArgumentError("The executable capability definition is not active")
        stored, payload = read_version_package(
            session,
            version_id=version.id,
            storage_root=storage_root,
        )
        files = read_archive(payload)
        try:
            manifest = PackageManifest.model_validate(json.loads(files["manifest.json"]))
        except (ValueError, KeyError) as exc:
            raise InvalidArgumentError(
                "An installable extension requires a valid manifest"
            ) from exc
        declared = {permission.name for permission in manifest.permissions}
        if not set(request.permission_ceiling) <= declared:
            raise InvalidArgumentError("Installation permissions exceed the package declaration")
        if manifest.package_name != definition.key:
            raise InvalidArgumentError("The manifest namespace differs from the capability key")
        if definition.type == "tool":
            validate_tool_exports(manifest, version.configuration, files)
        else:
            validate_published_exports(manifest, version.configuration)
        installation = Installation(
            id=uuid4(),
            version_id=version.id,
            requested_by=user.user_id,
            namespace=manifest.package_name,
            package_digest=stored.digest,
            status="requested",
            manifest=manifest.model_dump(mode="json"),
            permission_ceiling=sorted(set(request.permission_ceiling)),
        )
        session.add(installation)
        session.flush()
        stage_execution(session, extension_work(installation.id, "install"))
        audit_change(session, user, "installation.requested", installation.id)
        return installation

    return run_idempotent_create(
        session=session,
        scope_id=user.user_id,
        actor_id=user.user_id,
        operation="extensions.install",
        idempotency_key=request.operation_id,
        request_payload=request.model_dump(mode="json"),
        get_existing=lambda identifier: load_installation(session, identifier),
        create=create,
        resource_id=lambda installation: installation.id,
    )


def validate_tool_exports(
    manifest: PackageManifest, configuration: dict[str, object], files: dict[str, bytes]
) -> None:
    from opsmesh.capabilities.tools.schemas import ToolConfiguration

    tool = ToolConfiguration.model_validate(configuration)
    if manifest.hooks or manifest.events or manifest.published_events:
        raise InvalidArgumentError("A tool package cannot install undeclared hooks or events")
    if {item.name for item in tool.operations} != {item.export_id for item in manifest.operations}:
        raise InvalidArgumentError("The tool operation set differs from its SDK manifest")
    effects = {
        "model": "model_call",
        "file_read": "read",
        "file_write": "write",
        "shell": "write",
        "network": "network",
        "database": "write",
        "external_api": "write",
    }
    for declaration in tool.operations:
        export = next(item for item in manifest.operations if item.export_id == declaration.name)
        if (
            export.input_schema.model_dump_schema() != declaration.input_schema
            or export.output_schema.model_dump_schema() != declaration.output_schema
            or set(export.effects) - {"none"} != {effects[item] for item in declaration.effects}
            or export.timeout_seconds != declaration.timeout_seconds
        ):
            raise InvalidArgumentError("The tool export differs from its published contract")
    if tool.execution_mode == "script" and tool.entrypoint not in files:
        raise InvalidArgumentError("The installed script resource is missing")
    if tool.execution_mode == "sdk_export":
        try:
            metadata = tomllib.loads(files["pyproject.toml"].decode())
            entrypoint = metadata["project"]["entry-points"]["opsmesh.extensions"][
                manifest.package_name
            ]
        except (KeyError, ValueError, UnicodeError, TypeError) as exc:
            raise InvalidArgumentError("The tool requires its declared SDK factory") from exc
        if entrypoint != tool.entrypoint:
            raise InvalidArgumentError("The installed SDK factory differs from the tool entrypoint")


def validate_published_exports(manifest: PackageManifest, configuration: dict[str, object]) -> None:
    """The directory owns the published declaration; install cannot enlarge it."""
    actual = manifest.model_dump(mode="json")
    schema_exports: tuple[OperationExport | HookExport, ...] = (
        *manifest.operations,
        *manifest.hooks,
    )
    for group in ("operations", "hooks", "events"):
        normalized: list[dict[str, Any]] = []
        for item in actual[group]:
            value = dict(item)
            value.pop("kind", None)
            for field in ("input_schema", "output_schema"):
                if field in value:
                    export = next(e for e in schema_exports if e.export_id == value["export_id"])
                    schema = getattr(export, field)
                    value[field] = schema.model_dump_schema()
            normalized.append(value)
        published = configuration.get(group, [])
        if normalized != published:
            raise InvalidArgumentError(f"Package {group} differ from the published declaration")
    published_permissions = configuration.get("permissions", [])
    if not isinstance(published_permissions, list) or sorted(
        p.name for p in manifest.permissions
    ) != sorted(published_permissions):
        raise InvalidArgumentError("Package permissions differ from the published declaration")


def audit_change(session: Session, user: CurrentUser, action: str, target_id: UUID) -> None:
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action=f"extensions.{action}",
            actor=AuditActor.from_user(user),
            source="platform",
            target_type="extension",
            target_id=str(target_id),
            summary=action,
            payload_json=canonical_json({}),
        ),
    )


def audit_invocation(session: Session, invocation: Invocation) -> None:
    snapshot = invocation.snapshot
    record_audit(
        session,
        AuditRecord(
            category="operation",
            action=f"extensions.invocation.{invocation.status}",
            actor=AuditActor(
                actor_type="user",
                actor_id=str(invocation.principal_id),
                configuration_id=UUID(str(snapshot["configuration_id"]))
                if snapshot.get("configuration_id")
                else None,
                installation_id=invocation.installation_id,
                instance_id=invocation.instance_id,
                invocation_id=invocation.id,
                package_key=str(snapshot["namespace"]),
                configuration_revision=str(invocation.instance_revision),
            ),
            source="platform",
            target_type="extension_invocation",
            target_id=str(invocation.id),
            summary=f"Invocation {invocation.status}",
            payload_json=canonical_json(
                {"revision": invocation.revision, "error_code": invocation.error_code}
            ),
        ),
    )


def change_installation(
    session: Session,
    *,
    user: CurrentUser,
    installation_id: UUID,
    expected_revision: int,
    status: str,
) -> Installation:
    require_admin(user)
    installation = load_installation(session, installation_id, lock=True)
    if installation.revision != expected_revision:
        raise ConflictError("Installation revision changed")
    allowed = {
        "enabled": {"disabled"},
        "disabled": {"enabled"},
        "draining": {"enabled", "disabled", "failed", "needs_attention"},
    }
    if installation.status not in allowed.get(status, set()):
        raise ConflictError("Invalid installation transition")
    installation.status = status
    installation.revision += 1
    if status in {"disabled", "draining"}:
        for deployment in session.scalars(
            select(ManagedDeployment)
            .join(ExtensionInstance, ExtensionInstance.id == ManagedDeployment.instance_id)
            .where(
                ExtensionInstance.installation_id == installation.id,
                ManagedDeployment.status != "stopped",
            )
            .order_by(ManagedDeployment.id)
            .with_for_update(of=ManagedDeployment)
        ):
            deployment.status = "stopping"
            deployment.revision += 1
            stage_signal(
                session,
                dispatch_id=uuid4(),
                workflow_id=extension_work(deployment.id, "start_worker").workflow_id,
                signal_name="stop_worker",
                signal_args=(),
            )
    if status == "draining":
        stage_execution(session, extension_work(installation.id, "uninstall", execution_id=uuid4()))
    audit_change(session, user, f"installation.{status}", installation.id)
    return installation


def configure_instance(
    session: Session,
    *,
    user: CurrentUser,
    request: InstanceConfiguration,
    scopes: ScopeAuthorization,
    instance_id: UUID | None = None,
    expected_revision: int | None = None,
    references: InstanceReferences,
) -> ExtensionInstance:
    scopes.admit(
        session,
        user_id=user.user_id,
        configuration_id=request.configuration_id,
    )
    installation = load_installation(session, request.installation_id)
    authorize_installation(session, installation, user=user)
    if installation.status != "enabled":
        raise ConflictError("The extension installation is not enabled")
    manifest = PackageManifest.model_validate(installation.manifest)
    if (manifest.execution.mode == "sandbox") != (request.environment_id is not None):
        raise InvalidArgumentError("Only sandbox instances require an explicit environment")
    if installation.artifact is None:
        raise ConflictError("The installation has no verified artifact")
    references.validate(session, user=user, configuration=request, artifact=installation.artifact)
    if not set(request.permissions) <= set(installation.permission_ceiling):
        raise InvalidArgumentError("Instance permissions exceed the installation ceiling")
    if (
        redact_sensitive_payload(request.configuration, rules=redaction_rules_for(session))
        != request.configuration
    ):
        raise InvalidArgumentError("Sensitive configuration is not supported")
    try:
        Draft202012Validator(manifest.configuration_schema.model_dump_schema()).validate(
            request.configuration
        )
    except SchemaValidationError as error:
        raise InvalidArgumentError("Configuration violates its declared schema") from error
    if len(json.dumps(request.configuration).encode()) > 65536:
        raise InvalidArgumentError("Instance configuration exceeds the size bound")
    if instance_id is None:
        instance = ExtensionInstance(id=uuid4(), owner_id=user.user_id, revision=0)
        session.add(instance)
    else:
        instance = authorize_instance(session, instance_id, user=user, scopes=scopes, lock=True)
        if instance.revision != expected_revision:
            raise ConflictError("Instance revision changed")
        if (
            session.scalar(
                select(ManagedDeployment.id)
                .where(
                    ManagedDeployment.instance_id == instance.id,
                    ManagedDeployment.status != "stopped",
                )
                .limit(1)
            )
            is not None
        ):
            raise ConflictError("Stop and reconcile the managed deployment before reconfiguration")
        # A replacement is explicit and cannot enlarge the owner's grants.
        instance.revision += 1
    for key, value in request.model_dump(exclude={"expected_revision"}).items():
        setattr(instance, key, value)
    instance.status = "enabled"
    session.flush()
    audit_change(session, user, "instance.configured", instance.id)
    return instance


def active_invocations(session: Session, installation_id: UUID) -> list[Invocation]:
    return list(
        session.scalars(
            select(Invocation)
            .where(
                Invocation.installation_id == installation_id,
                Invocation.status.in_(["queued", "started", "needs_attention"]),
            )
            .order_by(Invocation.id)
        )
    )
