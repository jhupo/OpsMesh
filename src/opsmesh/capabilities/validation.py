"""Common manifest validation shared by all seven capability types.

These checks are pure: they read declared values and package bytes and
never import user code, connect to a server or start a process. The
type-specific configuration contracts live in the owning sub-package
services; what is common to every type — resource manifests, fixed
dependencies, flow template declarations, parameter schemas and secret
slots — is validated here so publish cannot trust an unvalidated draft.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from opsmesh.capabilities.models import (
    CAPABILITY_TYPES,
    MAX_DESCRIPTION_LENGTH,
    MAX_KEY_LENGTH,
    MAX_NAME_LENGTH,
)
from opsmesh.capabilities.packages import package_digest
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.shared.files import validate_relative_path
from opsmesh.shared.serialization import payload_digest

MAX_SCHEMA_BYTES = 65_536
MAX_MANIFEST_ENTRIES = 512
MAX_DEPENDENCIES = 128
MAX_SECRET_SLOTS = 64
MAX_SECRET_NAME_LENGTH = 100
DIGEST_LENGTH = 64


@dataclass(frozen=True)
class PackageIntake:
    """What one type service accepts from a package after its own checks.

    A type service may derive content from the package (a skill parses its
    SKILL.md) or only verify declarations against it; either way the result
    is the validated manifests the version row stores.
    """

    configuration: dict[str, object]
    resource_manifest: list[dict[str, object]]
    flow_templates: list[dict[str, object]]


def validate_key(key: object) -> str:
    """A definition key is a stable, non-empty machine name."""
    if not isinstance(key, str) or not key.strip():
        raise InvalidArgumentError("A capability key is required")
    if len(key) > MAX_KEY_LENGTH:
        raise InvalidArgumentError("The capability key is too long")
    return key


def validate_name(name: object) -> str:
    if not isinstance(name, str) or not name.strip():
        raise InvalidArgumentError("A capability name is required")
    if len(name) > MAX_NAME_LENGTH:
        raise InvalidArgumentError("The capability name is too long")
    return name


def validate_description(description: object) -> str:
    if description is None:
        return ""
    if not isinstance(description, str):
        raise InvalidArgumentError("The capability description must be text")
    if len(description) > MAX_DESCRIPTION_LENGTH:
        raise InvalidArgumentError("The capability description is too long")
    return description


def validate_configuration(configuration: object) -> dict[str, object]:
    """Every type configuration is one JSON object; shape is type-specific."""
    if not isinstance(configuration, dict):
        raise InvalidArgumentError("A capability configuration must be a JSON object")
    if not all(isinstance(key, str) for key in configuration):
        raise InvalidArgumentError("Configuration keys must be strings")
    return configuration


def validate_digest(digest: object, *, field: str) -> str:
    if not isinstance(digest, str) or len(digest) != DIGEST_LENGTH:
        raise InvalidArgumentError(f"The {field} must be a SHA-256 hex digest")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise InvalidArgumentError(f"The {field} must be a SHA-256 hex digest") from exc
    return digest.lower()


def validate_positive_size(size: object, *, field: str) -> int:
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise InvalidArgumentError(f"The {field} must be a non-negative byte count")
    return size


def validate_resource_manifest(manifest: object) -> list[dict[str, object]]:
    """Validate the declared resource index: paths, digests and sizes."""
    if manifest is None:
        return []
    if not isinstance(manifest, list):
        raise InvalidArgumentError("The resource manifest must be a list")
    if len(manifest) > MAX_MANIFEST_ENTRIES:
        raise InvalidArgumentError("The resource manifest declares too many entries")
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in manifest:
        if not isinstance(raw, dict):
            raise InvalidArgumentError("Every resource manifest entry must be an object")
        path = _validated_entry_path(raw.get("path"), field="resource path")
        if path in seen:
            raise InvalidArgumentError(
                "The resource manifest declares one path twice", details={"path": path}
            )
        seen.add(path)
        entry: dict[str, object] = {
            "path": path,
            "digest": validate_digest(raw.get("digest"), field="resource digest"),
            "size": validate_positive_size(raw.get("size"), field="resource size"),
        }
        media_type = raw.get("media_type")
        if media_type is not None:
            if not isinstance(media_type, str) or not media_type.strip():
                raise InvalidArgumentError("A resource media type must be non-empty text")
            entry["media_type"] = media_type
        entries.append(entry)
    return entries


def validate_dependencies(dependencies: object) -> list[dict[str, object]]:
    """Validate fixed dependency declarations: type, key and exact version."""
    if dependencies is None:
        return []
    if not isinstance(dependencies, list):
        raise InvalidArgumentError("The fixed dependencies must be a list")
    if len(dependencies) > MAX_DEPENDENCIES:
        raise InvalidArgumentError("A version declares too many fixed dependencies")
    entries: list[dict[str, object]] = []
    seen: set[tuple[str, str, int]] = set()
    for raw in dependencies:
        if not isinstance(raw, dict):
            raise InvalidArgumentError("Every fixed dependency must be an object")
        dependency_type = raw.get("type")
        if dependency_type not in CAPABILITY_TYPES:
            raise InvalidArgumentError(
                "A fixed dependency names an unknown capability type",
                details={"known_types": list(CAPABILITY_TYPES)},
            )
        key = validate_key(raw.get("key"))
        version = raw.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise InvalidArgumentError("A fixed dependency names a positive version")
        identity = (str(dependency_type), key, version)
        if identity in seen:
            raise InvalidArgumentError(
                "A version declares the same fixed dependency twice",
                details={"type": identity[0], "key": key, "version": version},
            )
        seen.add(identity)
        entries.append({"type": identity[0], "key": key, "version": version})
    return entries


def validate_flow_templates(flow_templates: object) -> list[dict[str, object]]:
    """Validate declared flow template resources: JSON files with digests."""
    if flow_templates is None:
        return []
    if not isinstance(flow_templates, list):
        raise InvalidArgumentError("The flow templates must be a list")
    if len(flow_templates) > MAX_MANIFEST_ENTRIES:
        raise InvalidArgumentError("A version declares too many flow templates")
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in flow_templates:
        if not isinstance(raw, dict):
            raise InvalidArgumentError("Every flow template declaration must be an object")
        path = _validated_entry_path(raw.get("path"), field="flow template path")
        if not path.endswith(".json"):
            raise InvalidArgumentError("A flow template must be a JSON resource")
        if path in seen:
            raise InvalidArgumentError(
                "A version declares the same flow template twice", details={"path": path}
            )
        seen.add(path)
        entries.append(
            {
                "path": path,
                "digest": validate_digest(raw.get("digest"), field="flow template digest"),
            }
        )
    return entries


def validate_parameter_schema(schema: object) -> dict[str, object]:
    """Validate one operation parameter schema as a bounded JSON Schema object."""
    if schema is None:
        return {"type": "object", "properties": {}, "required": []}
    if not isinstance(schema, dict):
        raise InvalidArgumentError("A parameter schema must be a JSON object")
    if len(json.dumps(schema, sort_keys=True, ensure_ascii=False)) > MAX_SCHEMA_BYTES:
        raise InvalidArgumentError("A parameter schema exceeds the permitted size")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise InvalidArgumentError(
            "A parameter schema is not a valid JSON Schema",
            details={"reason": exc.message},
        ) from exc
    return schema


def validate_secret_slots(slots: object) -> list[str]:
    """Validate declared secret slot names; values never travel in a manifest."""
    if slots is None:
        return []
    if not isinstance(slots, list):
        raise InvalidArgumentError("The secret slots must be a list")
    if len(slots) > MAX_SECRET_SLOTS:
        raise InvalidArgumentError("A version declares too many secret slots")
    names: list[str] = []
    for raw in slots:
        if not isinstance(raw, str) or not raw.strip():
            raise InvalidArgumentError("Every secret slot name must be non-empty text")
        if len(raw) > MAX_SECRET_NAME_LENGTH:
            raise InvalidArgumentError("A secret slot name is too long")
        if raw in names:
            raise InvalidArgumentError(
                "A version declares the same secret slot twice", details={"slot": raw}
            )
        names.append(raw)
    return names


def verify_declared_resources(
    files: Mapping[str, bytes], manifest: Sequence[Mapping[str, object]]
) -> None:
    """Every declared resource must exist in the package with its digest."""
    for entry in manifest:
        path = str(entry["path"])
        payload = files.get(path)
        if payload is None:
            raise InvalidArgumentError(
                "A declared resource is missing from the package", details={"path": path}
            )
        expected_digest = str(entry["digest"])
        if package_digest(payload) != expected_digest:
            raise InvalidArgumentError(
                "A declared resource does not match its digest", details={"path": path}
            )
        expected_size = entry.get("size")
        if isinstance(expected_size, int) and expected_size != len(payload):
            raise InvalidArgumentError(
                "A declared resource does not match its declared size", details={"path": path}
            )


def verify_flow_template_files(
    files: Mapping[str, bytes], templates: Sequence[Mapping[str, object]]
) -> None:
    """Every declared flow template must exist, digest and parse as JSON."""
    for entry in templates:
        path = str(entry["path"])
        payload = files.get(path)
        if payload is None:
            raise InvalidArgumentError(
                "A declared flow template is missing from the package", details={"path": path}
            )
        if package_digest(payload) != str(entry["digest"]):
            raise InvalidArgumentError(
                "A declared flow template does not match its digest", details={"path": path}
            )
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidArgumentError(
                "A declared flow template is not valid JSON", details={"path": path}
            ) from exc
        if not isinstance(document, dict):
            raise InvalidArgumentError(
                "A declared flow template must be a JSON object", details={"path": path}
            )


def detect_dependency_cycle(edges: Mapping[str, Iterable[str]], *, subject: str) -> None:
    """Refuse a directed graph that cannot be ordered.

    ``edges`` maps one node to the nodes it requires; a cycle means no
    valid order exists. The check is pure so a team handoff graph and a
    fixed dependency graph are refused before anything is written.
    """
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(node: str, trail: tuple[str, ...]) -> None:
        if node in visited:
            return
        if node in visiting:
            # ``visiting`` is exactly the set of nodes on the current trail.
            cycle = [*trail[trail.index(node) :], node]
            raise ConflictError(
                f"The {subject} form a cycle",
                details={"cycle": cycle},
            )
        visiting.add(node)
        for dependency in edges.get(node, ()):
            walk(dependency, (*trail, node))
        visiting.discard(node)
        visited.add(node)

    for start in sorted(edges):
        walk(start, ())


def version_digest(
    *,
    capability_type: str,
    configuration: Mapping[str, object],
    resource_manifest: Sequence[Mapping[str, object]],
    dependencies: Sequence[Mapping[str, object]],
    flow_templates: Sequence[Mapping[str, object]],
    package_digest: str | None,
) -> str:
    """The one digest a published version is frozen under."""
    return payload_digest(
        {
            "type": capability_type,
            "configuration": dict(configuration),
            "resource_manifest": [dict(entry) for entry in resource_manifest],
            "dependencies": [dict(entry) for entry in dependencies],
            "flow_templates": [dict(entry) for entry in flow_templates],
            "package_digest": package_digest,
        }
    )


def _validated_entry_path(path: object, *, field: str) -> str:
    if not isinstance(path, str):
        raise InvalidArgumentError(f"The {field} must be text")
    try:
        return validate_relative_path(path)
    except ValueError as exc:
        raise InvalidArgumentError(f"The {field} is not a safe relative path") from exc
