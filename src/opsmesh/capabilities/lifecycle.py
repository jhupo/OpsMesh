"""Definition and version lifecycle: drafts, publishing, status, visibility.

This module owns the capability transactions. A definition is created with
one draft version; every content change replaces or appends a draft;
publishing validates the draft completely, freezes it under one digest and
verifies its immutable package. Published versions are never written
again — no code path updates them. Authorization is the caller's job: the
service receives the verified actor and viewer, and the routes own the
authentication.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from opsmesh.capabilities.experts import service as expert_service
from opsmesh.capabilities.mcp import service as mcp_service
from opsmesh.capabilities.models import (
    CAPABILITY_TYPE_EXPERT,
    CAPABILITY_TYPE_MCP,
    CAPABILITY_TYPE_SKILL,
    CAPABILITY_TYPE_TEAM,
    CAPABILITY_TYPE_TOOL,
    CAPABILITY_TYPES,
    DEFINITION_STATUS_ACTIVE,
    DEFINITION_STATUS_DELETED,
    DEFINITION_STATUSES,
    SOURCE_SYSTEM,
    VERSION_STATUS_DRAFT,
    VERSION_STATUS_PUBLISHED,
    VISIBILITIES,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    CapabilityDefinition,
    CapabilityPackage,
    CapabilityVersion,
)
from opsmesh.capabilities.packages import (
    StoredPackage,
    build_package_archive,
    package_digest,
    read_archive,
    read_package,
    save_package,
    save_package_files,
)
from opsmesh.capabilities.skills import service as skill_service
from opsmesh.capabilities.teams import service as team_service
from opsmesh.capabilities.tools import service as tool_service
from opsmesh.capabilities.validation import (
    PackageIntake,
    validate_dependencies,
    validate_description,
    validate_flow_templates,
    validate_key,
    validate_name,
    validate_resource_manifest,
    version_digest,
)
from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.shared.db.pagination import page_scalars
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams

ConfigurationValidator = Callable[[Mapping[str, object]], dict[str, object]]
PackageIntakeHandler = Callable[..., PackageIntake]
VersionValidator = Callable[[Session, CapabilityVersion], None]

CONFIGURED_TYPES = (
    CAPABILITY_TYPE_EXPERT,
    CAPABILITY_TYPE_TEAM,
    CAPABILITY_TYPE_MCP,
    CAPABILITY_TYPE_SKILL,
    CAPABILITY_TYPE_TOOL,
)

_CONFIGURATION_VALIDATORS: dict[str, ConfigurationValidator] = {
    CAPABILITY_TYPE_EXPERT: expert_service.validate_configuration,
    CAPABILITY_TYPE_TEAM: team_service.validate_configuration,
    CAPABILITY_TYPE_MCP: mcp_service.validate_configuration,
    CAPABILITY_TYPE_SKILL: skill_service.validate_configuration,
    CAPABILITY_TYPE_TOOL: tool_service.validate_configuration,
}
_PACKAGE_INTAKE: dict[str, PackageIntakeHandler] = {
    CAPABILITY_TYPE_EXPERT: expert_service.intake_package,
    CAPABILITY_TYPE_TEAM: team_service.intake_package,
    CAPABILITY_TYPE_MCP: mcp_service.intake_package,
    CAPABILITY_TYPE_SKILL: skill_service.intake_package,
    CAPABILITY_TYPE_TOOL: tool_service.intake_package,
}
_VERSION_VALIDATORS: dict[str, VersionValidator] = {
    CAPABILITY_TYPE_EXPERT: expert_service.validate_version,
    CAPABILITY_TYPE_TEAM: team_service.validate_version,
    CAPABILITY_TYPE_MCP: mcp_service.validate_version,
    CAPABILITY_TYPE_SKILL: skill_service.validate_version,
    CAPABILITY_TYPE_TOOL: tool_service.validate_version,
}


@dataclass(frozen=True)
class Viewer:
    """Who reads the catalog; the routes build it from the verified identity."""

    user_id: UUID | None
    is_platform_admin: bool = False


@dataclass(frozen=True)
class DraftContent:
    """One fully specified draft: configuration, manifests and package."""

    configuration: dict[str, object] | None
    resource_manifest: list[dict[str, object]]
    dependencies: list[dict[str, object]]
    flow_templates: list[dict[str, object]]
    stored_package: StoredPackage | None
    filename: str | None


def create_definition(
    session: Session,
    *,
    capability_type: str,
    key: str,
    name: str,
    description: str | None,
    visibility: str,
    configuration: Mapping[str, object] | None,
    resource_manifest: Sequence[Mapping[str, object]] | None = None,
    dependencies: Sequence[Mapping[str, object]] | None = None,
    flow_templates: Sequence[Mapping[str, object]] | None = None,
    package: bytes | None = None,
    filename: str | None = None,
    actor: AuditActor,
    storage_root: Path,
    source: str = "user",
    files: Mapping[str, bytes] | None = None,
) -> tuple[CapabilityDefinition, CapabilityVersion]:
    """Create one definition with its first draft version."""
    if capability_type not in CAPABILITY_TYPES:
        raise InvalidArgumentError(
            "The capability type is unknown", details={"known_types": list(CAPABILITY_TYPES)}
        )
    validated_key = validate_key(key)
    validated_name = validate_name(name)
    validated_description = validate_description(description)
    if visibility not in VISIBILITIES:
        raise InvalidArgumentError("The visibility is unknown")
    if session.scalar(
        select(CapabilityDefinition.id).where(
            CapabilityDefinition.type == capability_type,
            CapabilityDefinition.key == validated_key,
            CapabilityDefinition.status != DEFINITION_STATUS_DELETED,
        )
    ):
        raise ConflictError(
            "A capability definition already uses this type and key",
            details={"type": capability_type, "key": validated_key},
        )
    content = validated_draft(
        capability_type=capability_type,
        configuration=configuration,
        resource_manifest=resource_manifest,
        dependencies=dependencies,
        flow_templates=flow_templates,
        package=package,
        filename=filename,
        storage_root=storage_root,
        files=files,
    )
    definition = CapabilityDefinition(
        type=capability_type,
        key=validated_key,
        name=validated_name,
        description=validated_description,
        visibility=visibility,
        status=DEFINITION_STATUS_ACTIVE,
        source=source,
        created_by_user_id=_actor_user_id(actor),
    )
    session.add(definition)
    session.flush()
    version = _new_draft_version(session, definition=definition, content=content)
    append_platform_event(
        session,
        actor=actor,
        action="capability.definition_created",
        target_type="capability_definition",
        target_id=str(definition.id),
        summary=f"Created {capability_type} definition {validated_key}",
        details={
            "type": capability_type,
            "key": validated_key,
            "visibility": visibility,
            "source": source,
            "version": version.version,
        },
    )
    return definition, version


def update_draft(
    session: Session,
    *,
    definition_id: UUID,
    configuration: Mapping[str, object] | None,
    resource_manifest: Sequence[Mapping[str, object]] | None = None,
    dependencies: Sequence[Mapping[str, object]] | None = None,
    flow_templates: Sequence[Mapping[str, object]] | None = None,
    package: bytes | None = None,
    filename: str | None = None,
    actor: AuditActor,
    storage_root: Path,
    files: Mapping[str, bytes] | None = None,
) -> CapabilityVersion:
    """Replace the current draft content; only the author may write it.

    A skill keeps the configuration its package derived when the caller
    sends none: its content comes from the package, not from JSON.
    """
    definition = _authorized_definition(session, definition_id=definition_id, actor=actor)
    draft = _current_draft(session, definition=definition)
    if configuration is None and definition.type == CAPABILITY_TYPE_SKILL and draft is not None:
        configuration = draft.configuration
    content = validated_draft(
        capability_type=definition.type,
        configuration=configuration,
        resource_manifest=resource_manifest,
        dependencies=dependencies,
        flow_templates=flow_templates,
        package=package,
        filename=filename,
        storage_root=storage_root,
        files=files,
    )
    if draft is None:
        version = _new_draft_version(session, definition=definition, content=content)
    else:
        version = _apply_draft_content(session, draft=draft, content=content)
    append_platform_event(
        session,
        actor=actor,
        action="capability.definition_updated",
        target_type="capability_definition",
        target_id=str(definition.id),
        summary=f"Updated the draft of {definition.type} definition {definition.key}",
        details={"version": version.version},
    )
    return version


def attach_package(
    session: Session,
    *,
    definition_id: UUID,
    package: bytes,
    filename: str,
    actor: AuditActor,
    storage_root: Path,
) -> CapabilityVersion:
    """Attach or replace the package of the current draft.

    A skill derives its whole configuration from the package; every other
    type verifies the package against the manifests its draft declares.
    The blob is stored under its digest immediately and the draft records
    the digest, so publish stores exactly the bytes that were validated.
    """
    definition = _authorized_definition(session, definition_id=definition_id, actor=actor)
    draft = _current_draft(session, definition=definition)
    if draft is None:
        raise ConflictError("The definition has no draft to attach a package to")
    configuration: Mapping[str, object] | None = draft.configuration
    if definition.type != CAPABILITY_TYPE_SKILL and not draft.configuration:
        configuration = None
    content = validated_draft(
        capability_type=definition.type,
        configuration=configuration,
        resource_manifest=draft.resource_manifest,
        dependencies=draft.dependencies,
        flow_templates=draft.flow_templates,
        package=package,
        filename=filename,
        storage_root=storage_root,
    )
    version = _apply_draft_content(session, draft=draft, content=content)
    append_platform_event(
        session,
        actor=actor,
        action="capability.package_attached",
        target_type="capability_definition",
        target_id=str(definition.id),
        summary=f"Attached a package to the draft of {definition.key}",
        details={"version": version.version, "filename": filename},
    )
    return version


def publish_version(
    session: Session,
    *,
    definition_id: UUID,
    version_number: int,
    actor: AuditActor,
    storage_root: Path,
) -> CapabilityVersion:
    """Validate one draft completely and freeze it under its digest.

    Publishing runs the full validation stack again — the type contract,
    the manifests and the fixed dependencies resolved to published
    versions — because a draft may have been staged before a contract
    changed, and because an internal caller must not be able to publish a
    shape the API would refuse.
    """
    definition = session.get(CapabilityDefinition, definition_id)
    if definition is None or definition.status == DEFINITION_STATUS_DELETED:
        raise NotFoundError("The capability definition does not exist")
    if definition.status != DEFINITION_STATUS_ACTIVE:
        raise ConflictError("The capability definition is disabled")
    version = session.scalar(
        select(CapabilityVersion).where(
            CapabilityVersion.definition_id == definition.id,
            CapabilityVersion.version == version_number,
        )
    )
    if version is None:
        raise NotFoundError("The capability version does not exist")
    if version.status != VERSION_STATUS_DRAFT:
        raise ConflictError("Only a draft version can be published")
    _authorize_writer(definition, actor=actor)
    content = validated_draft(
        capability_type=definition.type,
        configuration=version.configuration,
        resource_manifest=version.resource_manifest,
        dependencies=version.dependencies,
        flow_templates=version.flow_templates,
        package=None,
        filename=None,
        storage_root=storage_root,
    )
    _resolve_dependencies(session, definition=definition, dependencies=content.dependencies)
    _VERSION_VALIDATORS[definition.type](session, version)
    if (
        content.configuration is not None
        and content.configuration.get("execution_mode") == "direct"
        and definition.source != SOURCE_SYSTEM
    ):
        raise ForbiddenError("Direct execution is reserved for system tools")
    if version.package_digest is not None:
        _verify_staged_package(session, version=version, storage_root=storage_root)
    version.status = VERSION_STATUS_PUBLISHED
    version.digest = version_digest(
        capability_type=definition.type,
        configuration=content.configuration or {},
        resource_manifest=content.resource_manifest,
        dependencies=content.dependencies,
        flow_templates=content.flow_templates,
        package_digest=version.package_digest,
    )
    version.published_at = datetime.now(UTC)
    version.published_by_user_id = _actor_user_id(actor)
    session.flush()
    append_platform_event(
        session,
        actor=actor,
        action="capability.version_published",
        target_type="capability_version",
        target_id=str(version.id),
        summary=f"Published {definition.key} version {version.version}",
        details={"digest": version.digest, "definition_id": str(definition.id)},
    )
    return version


def set_definition_status(
    session: Session,
    *,
    definition_id: UUID,
    status: str,
    actor: AuditActor,
    actor_is_platform_admin: bool,
) -> CapabilityDefinition:
    """Enable, disable or delete one definition; deletion checks references."""
    if status not in DEFINITION_STATUSES:
        raise InvalidArgumentError("The definition status is unknown")
    definition = session.get(CapabilityDefinition, definition_id)
    if definition is None or definition.status == DEFINITION_STATUS_DELETED:
        raise NotFoundError("The capability definition does not exist")
    _authorize_status_writer(
        definition, actor=actor, actor_is_platform_admin=actor_is_platform_admin
    )
    if status == definition.status:
        return definition
    if status == DEFINITION_STATUS_DELETED:
        _guard_no_references(session, definition=definition)
    previous = definition.status
    definition.status = status
    append_platform_event(
        session,
        actor=actor,
        action="capability.definition_status_updated",
        target_type="capability_definition",
        target_id=str(definition.id),
        summary=f"Set {definition.key} status to {status}",
        details={"previous_status": previous, "status": status},
    )
    return definition


def set_visibility(
    session: Session,
    *,
    definition_id: UUID,
    visibility: str,
    actor: AuditActor,
) -> CapabilityDefinition:
    """Change visibility; a public definition may only pin public ones."""
    if visibility not in VISIBILITIES:
        raise InvalidArgumentError("The visibility is unknown")
    definition = _authorized_definition(session, definition_id=definition_id, actor=actor)
    if visibility == definition.visibility:
        return definition
    if visibility == VISIBILITY_PUBLIC:
        _guard_public_dependencies(session, definition=definition)
    previous = definition.visibility
    definition.visibility = visibility
    append_platform_event(
        session,
        actor=actor,
        action="capability.visibility_updated",
        target_type="capability_definition",
        target_id=str(definition.id),
        summary=f"Set {definition.key} visibility to {visibility}",
        details={"previous_visibility": previous, "visibility": visibility},
    )
    return definition


def update_definition_metadata(
    session: Session,
    *,
    definition_id: UUID,
    name: str | None,
    description: str | None,
    actor: AuditActor,
) -> CapabilityDefinition:
    """Change the definition-level name and description; content is versioned."""
    definition = _authorized_definition(session, definition_id=definition_id, actor=actor)
    changed: list[str] = []
    if name is not None:
        validated = validate_name(name)
        if validated != definition.name:
            definition.name = validated
            changed.append("name")
    if description is not None:
        validated = validate_description(description)
        if validated != definition.description:
            definition.description = validated
            changed.append("description")
    if changed:
        append_platform_event(
            session,
            actor=actor,
            action="capability.definition_updated",
            target_type="capability_definition",
            target_id=str(definition.id),
            summary=f"Updated the metadata of {definition.key}",
            details={"changed": changed},
        )
    return definition


def get_definition(
    session: Session, *, definition_id: UUID, viewer: Viewer
) -> CapabilityDefinition:
    definition = session.get(CapabilityDefinition, definition_id)
    if definition is None or not _visible(definition, viewer=viewer):
        raise NotFoundError("The capability definition does not exist")
    return definition


def list_definitions(
    session: Session,
    *,
    page: PageParams,
    viewer: Viewer,
    capability_type: str | None = None,
    visibility: str | None = None,
    source: str | None = None,
    status: str | None = None,
    key_query: str | None = None,
) -> tuple[list[CapabilityDefinition], int]:
    """One page of the catalog the viewer may see."""
    query = select(CapabilityDefinition).where(
        CapabilityDefinition.status != DEFINITION_STATUS_DELETED
    )
    if not viewer.is_platform_admin:
        visible = [CapabilityDefinition.visibility == VISIBILITY_PUBLIC]
        if viewer.user_id is not None:
            visible.append(CapabilityDefinition.created_by_user_id == viewer.user_id)
        query = query.where(or_(*visible))
    if capability_type is not None:
        query = query.where(CapabilityDefinition.type == capability_type)
    if visibility is not None:
        query = query.where(CapabilityDefinition.visibility == visibility)
    if source is not None:
        query = query.where(CapabilityDefinition.source == source)
    if status is not None:
        query = query.where(CapabilityDefinition.status == status)
    if key_query:
        query = query.where(CapabilityDefinition.key.ilike(f"%{key_query}%"))
    return page_scalars(
        session,
        query.order_by(CapabilityDefinition.created_at.desc(), CapabilityDefinition.id.desc()),
        page,
    )


def definition_summaries(
    session: Session, *, definitions: Sequence[CapabilityDefinition]
) -> dict[UUID, tuple[int, bool]]:
    """One grouped read of (latest version, has published version) per definition."""
    if not definitions:
        return {}
    rows = session.execute(
        select(
            CapabilityVersion.definition_id,
            func.max(CapabilityVersion.version),
            func.max(case((CapabilityVersion.status == VERSION_STATUS_PUBLISHED, 1), else_=0)),
        ).where(CapabilityVersion.definition_id.in_([item.id for item in definitions]))
        .group_by(CapabilityVersion.definition_id)
    ).all()
    return {
        row[0]: (int(row[1] or 0), bool(row[2])) for row in rows
    }


def list_versions(
    session: Session, *, definition_id: UUID, viewer: Viewer
) -> list[CapabilityVersion]:
    definition = get_definition(session, definition_id=definition_id, viewer=viewer)
    return list(
        session.scalars(
            select(CapabilityVersion)
            .where(CapabilityVersion.definition_id == definition.id)
            .order_by(CapabilityVersion.version.desc())
        )
    )


def get_version(
    session: Session, *, definition_id: UUID, version_number: int, viewer: Viewer
) -> CapabilityVersion:
    definition = get_definition(session, definition_id=definition_id, viewer=viewer)
    version = session.scalar(
        select(CapabilityVersion).where(
            CapabilityVersion.definition_id == definition.id,
            CapabilityVersion.version == version_number,
        )
    )
    if version is None:
        raise NotFoundError("The capability version does not exist")
    return version


def version_package_filename(session: Session, *, version_id: UUID) -> str | None:
    """The staged package filename of one version, if it has one."""
    return session.scalar(
        select(CapabilityPackage.filename).where(CapabilityPackage.version_id == version_id)
    )


def read_version_package(
    session: Session,
    *,
    version_id: UUID,
    storage_root: Path,
) -> tuple[CapabilityPackage, bytes]:
    """Read one stored package back, verifying its digest on every read."""
    stored = session.scalar(
        select(CapabilityPackage).where(CapabilityPackage.version_id == version_id)
    )
    if stored is None:
        raise NotFoundError("The capability version has no package")
    payload = read_package(
        storage_root,
        StoredPackage(digest=stored.digest, size=stored.size, storage_key=stored.storage_key),
    )
    return stored, payload


def validated_draft(
    *,
    capability_type: str,
    configuration: Mapping[str, object] | None,
    resource_manifest: Sequence[Mapping[str, object]] | None,
    dependencies: Sequence[Mapping[str, object]] | None,
    flow_templates: Sequence[Mapping[str, object]] | None,
    package: bytes | None,
    filename: str | None,
    storage_root: Path,
    files: Mapping[str, bytes] | None = None,
) -> DraftContent:
    """Run the whole validation stack for one draft staging.

    The type service validates the configuration, the root contract
    validates the manifests, and a present package — uploaded bytes or an
    already-read release directory — is taken in by the type service,
    which may derive content from it. The package blob is stored under
    its digest immediately; the draft records the digest so publish
    verifies exactly the content that was validated.
    """
    if (package is not None or files is not None) and not filename:
        raise InvalidArgumentError("A package upload must declare its filename")
    package_files = read_archive(package) if package is not None else files
    content = _intake_content(
        capability_type=capability_type,
        configuration=configuration,
        resource_manifest=resource_manifest,
        dependencies=dependencies,
        flow_templates=flow_templates,
        files=package_files,
    )
    stored: StoredPackage | None = None
    if package is not None:
        stored = save_package(storage_root, filename=filename or "", payload=package)
    elif package_files is not None:
        stored = save_package_files(storage_root, filename=filename or "", files=package_files)
    return DraftContent(
        configuration=content.configuration,
        resource_manifest=content.resource_manifest,
        dependencies=content.dependencies,
        flow_templates=content.flow_templates,
        stored_package=stored,
        filename=filename,
    )


def preview_version_digest(
    *,
    capability_type: str,
    configuration: Mapping[str, object] | None,
    resource_manifest: Sequence[Mapping[str, object]] | None,
    dependencies: Sequence[Mapping[str, object]] | None,
    flow_templates: Sequence[Mapping[str, object]] | None,
    files: Mapping[str, bytes] | None = None,
) -> str:
    """The digest a version of this content would publish under.

    Provisioning compares this against the stored digest to decide whether
    a release entry is already current, without staging anything.
    """
    content = _intake_content(
        capability_type=capability_type,
        configuration=configuration,
        resource_manifest=resource_manifest,
        dependencies=dependencies,
        flow_templates=flow_templates,
        files=files,
    )
    return version_digest(
        capability_type=capability_type,
        configuration=content.configuration or {},
        resource_manifest=content.resource_manifest,
        dependencies=content.dependencies,
        flow_templates=content.flow_templates,
        package_digest=(
            package_digest(build_package_archive(files)) if files is not None else None
        ),
    )


def _intake_content(
    *,
    capability_type: str,
    configuration: Mapping[str, object] | None,
    resource_manifest: Sequence[Mapping[str, object]] | None,
    dependencies: Sequence[Mapping[str, object]] | None,
    flow_templates: Sequence[Mapping[str, object]] | None,
    files: Mapping[str, bytes] | None,
) -> DraftContent:
    """Validate configuration and manifests, taking in any package content."""
    if capability_type not in _CONFIGURATION_VALIDATORS:
        raise InvalidArgumentError(
            "The capability type has no configuration contract in this step",
            details={"type": capability_type},
        )
    manifest = validate_resource_manifest(list(resource_manifest or []))
    declared_dependencies = validate_dependencies(list(dependencies or []))
    templates = validate_flow_templates(list(flow_templates or []))
    validated_config: dict[str, object] | None = None
    if configuration is not None:
        validated_config = _CONFIGURATION_VALIDATORS[capability_type](configuration)
    elif capability_type != CAPABILITY_TYPE_SKILL:
        raise InvalidArgumentError("The capability configuration is required")
    if files is not None:
        intake = _PACKAGE_INTAKE[capability_type](
            files,
            configuration=validated_config or {},
            resource_manifest=manifest,
            flow_templates=templates,
        )
        validated_config = intake.configuration
        manifest = validate_resource_manifest(intake.resource_manifest)
        templates = validate_flow_templates(intake.flow_templates)
    return DraftContent(
        configuration=validated_config,
        resource_manifest=manifest,
        dependencies=declared_dependencies,
        flow_templates=templates,
        stored_package=None,
        filename=None,
    )


def _new_draft_version(
    session: Session, *, definition: CapabilityDefinition, content: DraftContent
) -> CapabilityVersion:
    version = CapabilityVersion(
        definition_id=definition.id,
        version=_latest_version_number(session, definition_id=definition.id) + 1,
        status=VERSION_STATUS_DRAFT,
        configuration=content.configuration or {},
        resource_manifest=content.resource_manifest,
        dependencies=content.dependencies,
        flow_templates=content.flow_templates,
        package_digest=content.stored_package.digest if content.stored_package else None,
        package_size=content.stored_package.size if content.stored_package else None,
    )
    session.add(version)
    session.flush()
    _store_package_row(session, version=version, content=content)
    return version


def _apply_draft_content(
    session: Session, *, draft: CapabilityVersion, content: DraftContent
) -> CapabilityVersion:
    draft.configuration = content.configuration or {}
    draft.resource_manifest = content.resource_manifest
    draft.dependencies = content.dependencies
    draft.flow_templates = content.flow_templates
    draft.package_digest = content.stored_package.digest if content.stored_package else None
    draft.package_size = content.stored_package.size if content.stored_package else None
    session.flush()
    _store_package_row(session, version=draft, content=content)
    return draft


def _store_package_row(
    session: Session, *, version: CapabilityVersion, content: DraftContent
) -> None:
    """Index the staged blob for this draft; the draft is mutable, the blob is not."""
    if content.stored_package is None or content.filename is None:
        return
    existing = session.scalar(
        select(CapabilityPackage).where(CapabilityPackage.version_id == version.id)
    )
    if existing is not None:
        existing.filename = content.filename
        existing.size = content.stored_package.size
        existing.digest = content.stored_package.digest
        existing.storage_key = content.stored_package.storage_key
        return
    session.add(
        CapabilityPackage(
            version_id=version.id,
            filename=content.filename,
            size=content.stored_package.size,
            digest=content.stored_package.digest,
            storage_key=content.stored_package.storage_key,
        )
    )
    session.flush()


def _verify_staged_package(
    session: Session, *, version: CapabilityVersion, storage_root: Path
) -> None:
    """The staged blob must still exist and still digest to what the draft says."""
    stored = session.scalar(
        select(CapabilityPackage).where(CapabilityPackage.version_id == version.id)
    )
    digest = version.package_digest or ""
    if stored is None or stored.digest != digest:
        raise ConflictError("The draft package index no longer matches its digest")
    read_package(
        storage_root,
        StoredPackage(digest=stored.digest, size=stored.size, storage_key=stored.storage_key),
    )


def _current_draft(
    session: Session, *, definition: CapabilityDefinition
) -> CapabilityVersion | None:
    latest = _latest_version_number(session, definition_id=definition.id)
    if latest == 0:
        return None
    version = session.scalar(
        select(CapabilityVersion).where(
            CapabilityVersion.definition_id == definition.id,
            CapabilityVersion.version == latest,
        )
    )
    if version is None or version.status != VERSION_STATUS_DRAFT:
        return None
    return version


def _latest_version_number(session: Session, *, definition_id: UUID) -> int:
    latest = session.scalar(
        select(CapabilityVersion.version)
        .where(CapabilityVersion.definition_id == definition_id)
        .order_by(CapabilityVersion.version.desc())
        .limit(1)
    )
    return int(latest or 0)


def _resolve_dependencies(
    session: Session,
    *,
    definition: CapabilityDefinition,
    dependencies: Sequence[Mapping[str, object]],
) -> None:
    """Every fixed dependency must resolve to one published, visible version."""
    for entry in dependencies:
        dependency_type = str(entry["type"])
        key = str(entry["key"])
        raw_version = entry["version"]
        if not isinstance(raw_version, int) or isinstance(raw_version, bool):
            raise ConflictError("A fixed dependency names a malformed version")
        target = session.scalar(
            select(CapabilityDefinition).where(
                CapabilityDefinition.type == dependency_type,
                CapabilityDefinition.key == key,
                CapabilityDefinition.status != DEFINITION_STATUS_DELETED,
            )
        )
        if target is None or target.status != DEFINITION_STATUS_ACTIVE:
            raise ConflictError(
                "A fixed dependency names a definition that is not active",
                details={"type": dependency_type, "key": key},
            )
        resolved = session.scalar(
            select(CapabilityVersion.id).where(
                CapabilityVersion.definition_id == target.id,
                CapabilityVersion.version == raw_version,
                CapabilityVersion.status == VERSION_STATUS_PUBLISHED,
            )
        )
        if resolved is None:
            raise ConflictError(
                "A fixed dependency names a version that is not published",
                details={"type": dependency_type, "key": key, "version": raw_version},
            )
        if (
            definition.visibility == VISIBILITY_PUBLIC
            and target.visibility != VISIBILITY_PUBLIC
        ):
            raise ConflictError(
                "A public definition cannot pin a private dependency",
                details={"key": key},
            )
        if (
            definition.visibility == VISIBILITY_PRIVATE
            and target.visibility != VISIBILITY_PUBLIC
            and target.created_by_user_id != definition.created_by_user_id
        ):
            raise ForbiddenError(
                "A private definition cannot pin another author's private dependency",
                details={"key": key},
            )


def _guard_no_references(session: Session, *, definition: CapabilityDefinition) -> None:
    """Refuse deleting a definition some version still pins."""
    version_numbers = list(
        session.scalars(
            select(CapabilityVersion.version).where(
                CapabilityVersion.definition_id == definition.id
            )
        )
    )
    if not version_numbers:
        return
    referencing = session.scalar(
        select(CapabilityVersion.id)
        .join(
            CapabilityDefinition,
            CapabilityDefinition.id == CapabilityVersion.definition_id,
        )
        .where(
            CapabilityDefinition.status != DEFINITION_STATUS_DELETED,
            CapabilityDefinition.id != definition.id,
            or_(
                *[
                    CapabilityVersion.dependencies.contains(
                        [
                            {
                                "type": definition.type,
                                "key": definition.key,
                                "version": number,
                            }
                        ]
                    )
                    for number in version_numbers
                ]
            ),
        )
        .limit(1)
    )
    if referencing is not None:
        raise ConflictError(
            "The capability definition is still pinned by another version",
            details={"type": definition.type, "key": definition.key},
        )


def _guard_public_dependencies(session: Session, *, definition: CapabilityDefinition) -> None:
    """A public definition may only pin definitions that are themselves public."""
    versions = list(
        session.scalars(
            select(CapabilityVersion).where(
                CapabilityVersion.definition_id == definition.id,
                CapabilityVersion.status == VERSION_STATUS_PUBLISHED,
            )
        )
    )
    for version in versions:
        for entry in version.dependencies:
            target = session.scalar(
                select(CapabilityDefinition).where(
                    CapabilityDefinition.type == entry.get("type"),
                    CapabilityDefinition.key == entry.get("key"),
                )
            )
            if target is None or target.visibility != VISIBILITY_PUBLIC:
                raise ConflictError(
                    "A public definition cannot pin a private dependency",
                    details={"key": entry.get("key")},
                )


def _authorized_definition(
    session: Session, *, definition_id: UUID, actor: AuditActor
) -> CapabilityDefinition:
    definition = session.get(CapabilityDefinition, definition_id)
    if definition is None or definition.status == DEFINITION_STATUS_DELETED:
        raise NotFoundError("The capability definition does not exist")
    if definition.status != DEFINITION_STATUS_ACTIVE:
        raise ConflictError("The capability definition is disabled")
    _authorize_writer(definition, actor=actor)
    return definition


def _authorize_writer(definition: CapabilityDefinition, *, actor: AuditActor) -> None:
    """Authors write their own definitions; the platform writes system ones.

    A ``platform`` actor is produced only by out-of-band operations such
    as release provisioning: HTTP routes always carry a verified user or
    delegation actor, so this path cannot be reached from a request.
    """
    if definition.source == SOURCE_SYSTEM:
        if actor.actor_type == "platform":
            return
        raise ForbiddenError("System capability definitions are provisioned, not edited")
    if actor.actor_type != "user" or actor.actor_id != str(definition.created_by_user_id):
        raise ForbiddenError("Only the capability creator can modify its definition")


def _authorize_status_writer(
    definition: CapabilityDefinition, *, actor: AuditActor, actor_is_platform_admin: bool
) -> None:
    if definition.source == SOURCE_SYSTEM:
        if not actor_is_platform_admin:
            raise ForbiddenError("Only a platform administrator manages system definitions")
        return
    is_author = actor.actor_type == "user" and actor.actor_id == str(definition.created_by_user_id)
    if not is_author and not actor_is_platform_admin:
        raise ForbiddenError("Only the capability creator can modify its definition")


def _actor_user_id(actor: AuditActor) -> UUID | None:
    """The author of one definition; a system release has no human author."""
    if actor.actor_type == "platform":
        return None
    if actor.actor_type != "user" or actor.actor_id is None:
        raise ForbiddenError("The capability author must be an authenticated user")
    try:
        return UUID(actor.actor_id)
    except ValueError as exc:
        raise ForbiddenError("The capability author identity is malformed") from exc


def _visible(definition: CapabilityDefinition, *, viewer: Viewer) -> bool:
    if definition.status == DEFINITION_STATUS_DELETED:
        return False
    if definition.visibility == VISIBILITY_PUBLIC:
        return True
    if viewer.user_id is not None and definition.created_by_user_id == viewer.user_id:
        return True
    return viewer.is_platform_admin


__all__ = [
    "CONFIGURED_TYPES",
    "DraftContent",
    "Viewer",
    "attach_package",
    "create_definition",
    "get_definition",
    "get_version",
    "list_definitions",
    "list_versions",
    "publish_version",
    "read_version_package",
    "set_definition_status",
    "set_visibility",
    "update_definition_metadata",
    "update_draft",
    "validated_draft",
]
