"""System built-in provisioning from the bundled release index.

The release index names every built-in package with its type, key,
version, path and expected digest. Provisioning verifies each package
directory against its digest, publishes the packages in the order the
index declares — a package may only pin packages the release already
provisioned — and is idempotent: an entry whose published version still
digests to the same value is skipped, and an entry that changed a
published version's content is refused instead of silently rewritten.

Nothing in a release package is imported or executed: provisioning runs
the same static validation stack as a user upload.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities import lifecycle
from opsmesh.capabilities.mcp.service import carries_package as mcp_carries_package
from opsmesh.capabilities.models import (
    CAPABILITY_TYPE_MCP,
    CAPABILITY_TYPE_SKILL,
    CAPABILITY_TYPES,
    SOURCE_SYSTEM,
    VISIBILITIES,
    VISIBILITY_PUBLIC,
    CapabilityDefinition,
    CapabilityVersion,
)
from opsmesh.capabilities.packages import (
    UnsafePackageError,
    directory_digest,
    package_digest,
    read_directory,
)
from opsmesh.capabilities.skills.schemas import skill_metadata
from opsmesh.capabilities.validation import (
    validate_dependencies,
    validate_flow_templates,
    validate_resource_manifest,
)
from opsmesh.governance.audit.service import AuditActor, append_platform_event
from opsmesh.shared.errors import ConflictError, InvalidArgumentError

RELEASE_INDEX_NAME = "manifest.json"
PACKAGE_MANIFEST_NAME = "manifest.json"
MAX_RELEASE_ENTRIES = 256


@dataclass(frozen=True)
class ReleaseEntry:
    """One built-in package as the release index declares it."""

    capability_type: str
    key: str
    version: int
    path: str
    digest: str


@dataclass(frozen=True)
class ProvisioningReport:
    """What one provisioning run did, entry by entry."""

    release: str
    published: tuple[str, ...]
    skipped: tuple[str, ...]


def read_release_index(release_root: Path) -> tuple[str, list[ReleaseEntry]]:
    """Parse and validate the bundled release index."""
    index_path = release_root / RELEASE_INDEX_NAME
    if not index_path.is_file():
        raise InvalidArgumentError("The release root has no manifest.json index")
    try:
        document = json.loads(index_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidArgumentError("The release index is not valid JSON") from exc
    if not isinstance(document, dict):
        raise InvalidArgumentError("The release index must be a JSON object")
    release = document.get("release")
    if not isinstance(release, str) or not release.strip():
        raise InvalidArgumentError("The release index must name its release")
    raw_packages = document.get("packages")
    if not isinstance(raw_packages, list) or not raw_packages:
        raise InvalidArgumentError("The release index must declare its packages")
    if len(raw_packages) > MAX_RELEASE_ENTRIES:
        raise InvalidArgumentError("The release index declares too many packages")
    entries: list[ReleaseEntry] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_packages:
        if not isinstance(raw, dict):
            raise InvalidArgumentError("Every release entry must be an object")
        capability_type = raw.get("type")
        if capability_type not in CAPABILITY_TYPES:
            raise InvalidArgumentError(
                "A release entry names an unknown capability type",
                details={"known_types": list(CAPABILITY_TYPES)},
            )
        key = raw.get("key")
        if not isinstance(key, str) or not key.strip():
            raise InvalidArgumentError("A release entry must name its key")
        version = raw.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise InvalidArgumentError("A release entry must name a positive version")
        path = raw.get("path")
        if not isinstance(path, str) or not path.strip() or path.startswith("/"):
            raise InvalidArgumentError("A release entry must name a relative package path")
        digest = raw.get("digest")
        if not isinstance(digest, str) or len(digest) != 64:
            raise InvalidArgumentError("A release entry must declare its package digest")
        identity = (str(capability_type), key)
        if identity in seen:
            raise InvalidArgumentError(
                "The release index declares one package twice",
                details={"type": identity[0], "key": key},
            )
        seen.add(identity)
        entries.append(
            ReleaseEntry(
                capability_type=str(capability_type),
                key=key,
                version=version,
                path=path,
                digest=digest.lower(),
            )
        )
    return release, entries


def provision_release(
    session: Session,
    *,
    release_root: Path,
    storage_root: Path,
    actor: AuditActor,
) -> ProvisioningReport:
    """Publish the built-in capabilities one release declares, idempotently."""
    release, entries = read_release_index(release_root)
    published: list[str] = []
    skipped: list[str] = []
    for entry in entries:
        files = _release_files(release_root, entry=entry)
        manifest = _package_manifest(files, entry=entry)
        dependencies = validate_dependencies(manifest.get("dependencies"))
        flow_templates = validate_flow_templates(manifest.get("flow_templates"))
        configuration = manifest.get("configuration")
        resource_manifest = _release_resource_manifest(entry=entry, files=files)
        staged_files = _staged_release_files(
            entry=entry, configuration=configuration, files=files
        )
        _require_provisioned_dependencies(session, dependencies=dependencies)
        existing = session.scalar(
            select(CapabilityDefinition).where(
                CapabilityDefinition.type == entry.capability_type,
                CapabilityDefinition.key == entry.key,
                CapabilityDefinition.status != "deleted",
            )
        )
        if existing is not None and existing.source != SOURCE_SYSTEM:
            raise ConflictError(
                "A user definition already owns this capability key",
                details={"type": entry.capability_type, "key": entry.key},
            )
        digest = lifecycle.preview_version_digest(
            capability_type=entry.capability_type,
            configuration=configuration,
            resource_manifest=resource_manifest,
            dependencies=dependencies,
            flow_templates=flow_templates,
            files=staged_files,
        )
        if existing is not None:
            stored_version = session.scalar(
                select(CapabilityVersion).where(
                    CapabilityVersion.definition_id == existing.id,
                    CapabilityVersion.version == entry.version,
                )
            )
            if stored_version is not None:
                if stored_version.digest == digest:
                    skipped.append(entry.key)
                    continue
                raise ConflictError(
                    "The release changed a published system version",
                    details={"key": entry.key, "version": entry.version},
                )
        if existing is None:
            definition, draft = lifecycle.create_definition(
                session,
                capability_type=entry.capability_type,
                key=entry.key,
                name=str(manifest.get("name") or entry.key),
                description=manifest.get("description"),
                visibility=str(manifest.get("visibility") or "public"),
                configuration=configuration,
                resource_manifest=resource_manifest,
                dependencies=dependencies,
                flow_templates=flow_templates,
                actor=actor,
                storage_root=storage_root,
                source=SOURCE_SYSTEM,
                files=staged_files,
                filename=f"{entry.key}.zip" if staged_files is not None else None,
            )
        else:
            draft = lifecycle.update_draft(
                session,
                definition_id=existing.id,
                configuration=configuration,
                resource_manifest=resource_manifest,
                dependencies=dependencies,
                flow_templates=flow_templates,
                actor=actor,
                storage_root=storage_root,
                files=staged_files,
                filename=f"{entry.key}.zip" if staged_files is not None else None,
            )
            definition = existing
        lifecycle.publish_version(
            session,
            definition_id=definition.id,
            version_number=draft.version,
            actor=actor,
            storage_root=storage_root,
        )
        published.append(entry.key)
    append_platform_event(
        session,
        actor=actor,
        action="capability.release_provisioned",
        target_type="capability_release",
        target_id=release,
        summary=f"Provisioned the {release} built-in capabilities",
        details={"published": published, "skipped": skipped},
    )
    return ProvisioningReport(
        release=release, published=tuple(published), skipped=tuple(skipped)
    )


def _release_files(release_root: Path, *, entry: ReleaseEntry) -> dict[str, bytes]:
    try:
        files = read_directory(release_root / entry.path)
    except (UnsafePackageError, OSError) as exc:
        raise InvalidArgumentError(
            "A release package directory cannot be read", details={"path": entry.path}
        ) from exc
    if directory_digest(files) != entry.digest:
        raise ConflictError(
            "A release package does not match its declared digest",
            details={"path": entry.path, "key": entry.key},
        )
    return files


def _package_manifest(files: Mapping[str, bytes], *, entry: ReleaseEntry) -> dict[str, Any]:
    """Parse one package manifest: the release's authoritative declaration.

    A standard skill package carries its metadata in the SKILL.md
    frontmatter and needs no separate manifest; every other type declares
    name, description, visibility, configuration, dependencies and flow
    templates in its manifest.json.
    """
    payload = files.get(PACKAGE_MANIFEST_NAME)
    if payload is None:
        if entry.capability_type != CAPABILITY_TYPE_SKILL:
            raise InvalidArgumentError(
                "A release package has no manifest.json", details={"path": entry.path}
            )
        name, description = skill_metadata(files)
        return {
            "name": name,
            "description": description,
            "visibility": VISIBILITY_PUBLIC,
            "configuration": None,
            "dependencies": [],
            "flow_templates": [],
        }
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidArgumentError(
            "A release package manifest is not valid JSON", details={"path": entry.path}
        ) from exc
    if not isinstance(document, dict):
        raise InvalidArgumentError(
            "A release package manifest must be a JSON object", details={"path": entry.path}
        )
    visibility = document.get("visibility", VISIBILITY_PUBLIC)
    if visibility not in VISIBILITIES:
        raise InvalidArgumentError(
            "A release package manifest declares an unknown visibility",
            details={"path": entry.path},
        )
    declared_name = document.get("name")
    if not isinstance(declared_name, str) or not declared_name.strip():
        raise InvalidArgumentError(
            "A release package manifest must name the capability",
            details={"path": entry.path},
        )
    return document


def _staged_release_files(
    *, entry: ReleaseEntry, configuration: object, files: dict[str, bytes]
) -> dict[str, bytes] | None:
    """The release files one entry stages as its package, if any.

    A remote MCP template publishes without a package: the release still
    verifies the directory against its index digest, but the version
    carries no blob, exactly as a user-declared remote template may not
    attach one. Every other entry stages its directory.
    """
    if (
        entry.capability_type == CAPABILITY_TYPE_MCP
        and isinstance(configuration, Mapping)
        and not mcp_carries_package(configuration)
    ):
        return None
    return files


def _release_resource_manifest(
    *, entry: ReleaseEntry, files: Mapping[str, bytes]
) -> list[dict[str, object]] | None:
    """The resource index of a release package, derived from its files.

    A skill derives its own index from the package (the SKILL.md is its
    configuration); every other type declares every release file except
    the manifest as a resource with its digest.
    """
    if entry.capability_type == "skill":
        return None
    manifest: list[dict[str, object]] = []
    for path in sorted(files):
        if path == PACKAGE_MANIFEST_NAME:
            continue
        manifest.append(
            {
                "path": path,
                "digest": package_digest(files[path]),
                "size": len(files[path]),
            }
        )
    return validate_resource_manifest(manifest)


def _require_provisioned_dependencies(
    session: Session, *, dependencies: Sequence[Mapping[str, object]]
) -> None:
    """Every pinned dependency must already be a published system version.

    This is what validates the release order: an entry that pins a
    package the release has not provisioned yet is refused, so the index
    cannot declare an unresolvable or cyclic order.
    """
    for entry in dependencies:
        target = session.scalar(
            select(CapabilityDefinition).where(
                CapabilityDefinition.type == entry.get("type"),
                CapabilityDefinition.key == entry.get("key"),
                CapabilityDefinition.source == SOURCE_SYSTEM,
                CapabilityDefinition.status != "deleted",
            )
        )
        if target is None:
            raise ConflictError(
                "A release package pins a dependency the release has not provisioned",
                details={"key": entry.get("key")},
            )
        resolved = session.scalar(
            select(CapabilityVersion.id).where(
                CapabilityVersion.definition_id == target.id,
                CapabilityVersion.version == entry.get("version"),
                CapabilityVersion.status == "published",
            )
        )
        if resolved is None:
            raise ConflictError(
                "A release package pins a dependency version that is not published",
                details={"key": entry.get("key"), "version": entry.get("version")},
            )


__all__ = [
    "ProvisioningReport",
    "ReleaseEntry",
    "provision_release",
    "read_release_index",
]
