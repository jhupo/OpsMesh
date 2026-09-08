from __future__ import annotations

import re
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from uuid import UUID

from backend.app.files.security import validate_storage_key
from backend.app.projects.models import AgentRunProjectSnapshot
from backend.app.projects.policy import require_path_within, validate_project_layout
from backend.app.projects.serialization import sha256_json

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class RunProjectFile:
    project_file_id: UUID
    version: int
    workspace_file_id: UUID
    project_path: str
    access_mode: str
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    storage_key: str


@dataclass(frozen=True, slots=True)
class RunProjectOutput:
    project_output_id: UUID
    project_path: str
    artifact_type: str
    content_type: str | None
    required: bool
    max_bytes: int


@dataclass(frozen=True, slots=True)
class RunProjectManifest:
    project_id: UUID
    slug: str
    input_path: str
    work_path: str
    output_path: str
    configuration_version_id: UUID
    configuration_version: int
    configuration_checksum_sha256: str
    configuration: dict[str, object]
    files: tuple[RunProjectFile, ...]
    outputs: tuple[RunProjectOutput, ...]


def parse_run_project_manifest(
    snapshot: AgentRunProjectSnapshot,
    *,
    workspace_id: UUID,
) -> RunProjectManifest:
    if snapshot.workspace_id != workspace_id or snapshot.schema_version != 1:
        raise ValueError("Run project snapshot scope or schema is invalid")
    manifest = snapshot.manifest
    if sha256_json(manifest) != snapshot.fingerprint_sha256:
        raise ValueError("Run project snapshot fingerprint does not match its manifest")
    if manifest.get("schema_version") != snapshot.schema_version:
        raise ValueError("Run project snapshot manifest schema is invalid")

    project = _mapping(manifest, "project")
    project_id = _uuid(project, "id")
    if project_id != snapshot.project_id:
        raise ValueError("Run project snapshot project identity is invalid")
    slug = _string(project, "slug", max_length=80)
    input_path, work_path, output_path = validate_project_layout(
        input_path=_string(project, "input_path", max_length=512),
        work_path=_string(project, "work_path", max_length=512),
        output_path=_string(project, "output_path", max_length=512),
    )

    configuration = _mapping(manifest, "configuration")
    configuration_version_id = _uuid(configuration, "id")
    if configuration_version_id != snapshot.configuration_version_id:
        raise ValueError("Run project snapshot configuration identity is invalid")
    configuration_version = _positive_int(configuration, "version")
    configuration_checksum = _checksum(configuration, "checksum_sha256")
    configuration_value = _mapping(configuration, "value")
    if sha256_json(configuration_value) != configuration_checksum:
        raise ValueError("Run project snapshot configuration checksum is invalid")

    files = tuple(
        _parse_file(item, workspace_id=workspace_id, input_path=input_path)
        for item in _object_list(manifest, "files")
    )
    outputs = tuple(
        _parse_output(item, output_path=output_path)
        for item in _object_list(manifest, "outputs")
    )
    _require_unique((item.project_file_id for item in files), "project file IDs")
    _require_unique((item.workspace_file_id for item in files), "workspace file IDs")
    _require_unique((item.project_path for item in files), "project input paths")
    _require_unique((item.project_output_id for item in outputs), "project output IDs")
    _require_unique((item.project_path for item in outputs), "project output paths")
    _require_leaf_paths((item.project_path for item in files), "project input paths")
    _require_leaf_paths((item.project_path for item in outputs), "project output paths")
    return RunProjectManifest(
        project_id=project_id,
        slug=slug,
        input_path=input_path,
        work_path=work_path,
        output_path=output_path,
        configuration_version_id=configuration_version_id,
        configuration_version=configuration_version,
        configuration_checksum_sha256=configuration_checksum,
        configuration=configuration_value,
        files=files,
        outputs=outputs,
    )


def public_run_project_manifest(manifest: RunProjectManifest) -> dict[str, object]:
    return {
        "project": {
            "id": str(manifest.project_id),
            "slug": manifest.slug,
            "input_path": manifest.input_path,
            "work_path": manifest.work_path,
            "output_path": manifest.output_path,
        },
        "configuration": {
            "id": str(manifest.configuration_version_id),
            "version": manifest.configuration_version,
            "checksum_sha256": manifest.configuration_checksum_sha256,
            "value": manifest.configuration,
        },
        "files": [
            {
                "project_file_id": str(item.project_file_id),
                "version": item.version,
                "workspace_file_id": str(item.workspace_file_id),
                "project_path": item.project_path,
                "access_mode": item.access_mode,
                "filename": item.filename,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
                "checksum_sha256": item.checksum_sha256,
            }
            for item in manifest.files
        ],
        "outputs": [
            {
                "project_output_id": str(item.project_output_id),
                "project_path": item.project_path,
                "artifact_type": item.artifact_type,
                "content_type": item.content_type,
                "required": item.required,
                "max_bytes": item.max_bytes,
            }
            for item in manifest.outputs
        ],
    }


def _parse_file(
    value: dict[str, object],
    *,
    workspace_id: UUID,
    input_path: str,
) -> RunProjectFile:
    storage_key = validate_storage_key(_string(value, "storage_key", max_length=512))
    if not storage_key.startswith(f"workspaces/{workspace_id}/files/"):
        raise ValueError("Run project file storage scope is invalid")
    access_mode = _string(value, "access_mode", max_length=32)
    if access_mode not in {"read_only", "copy_on_write"}:
        raise ValueError("Run project file access mode is invalid")
    return RunProjectFile(
        project_file_id=_uuid(value, "project_file_id"),
        version=_positive_int(value, "version"),
        workspace_file_id=_uuid(value, "workspace_file_id"),
        project_path=require_path_within(
            _string(value, "project_path", max_length=512),
            input_path,
            kind="input",
        ),
        access_mode=access_mode,
        filename=_string(value, "filename", max_length=260),
        content_type=_string(value, "content_type", max_length=120),
        size_bytes=_non_negative_int(value, "size_bytes"),
        checksum_sha256=_checksum(value, "checksum_sha256"),
        storage_key=storage_key,
    )


def _parse_output(value: dict[str, object], *, output_path: str) -> RunProjectOutput:
    required = value.get("required")
    if not isinstance(required, bool):
        raise ValueError("Run project output required flag is invalid")
    content_type = value.get("content_type")
    if content_type is not None and (
        not isinstance(content_type, str) or not content_type or len(content_type) > 120
    ):
        raise ValueError("Run project output content type is invalid")
    return RunProjectOutput(
        project_output_id=_uuid(value, "project_output_id"),
        project_path=require_path_within(
            _string(value, "project_path", max_length=512),
            output_path,
            kind="output",
        ),
        artifact_type=_string(value, "artifact_type", max_length=80),
        content_type=content_type,
        required=required,
        max_bytes=_positive_int(value, "max_bytes"),
    )


def _mapping(value: dict[str, object], key: str) -> dict[str, object]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise ValueError(f"Run project snapshot {key} is invalid")
    return item


def _object_list(value: dict[str, object], key: str) -> list[dict[str, object]]:
    items = value.get(key)
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError(f"Run project snapshot {key} is invalid")
    return items


def _string(value: dict[str, object], key: str, *, max_length: int) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item or len(item) > max_length:
        raise ValueError(f"Run project snapshot {key} is invalid")
    return item


def _uuid(value: dict[str, object], key: str) -> UUID:
    try:
        return UUID(_string(value, key, max_length=36))
    except ValueError as exc:
        raise ValueError(f"Run project snapshot {key} is invalid") from exc


def _positive_int(value: dict[str, object], key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
        raise ValueError(f"Run project snapshot {key} is invalid")
    return item


def _non_negative_int(value: dict[str, object], key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise ValueError(f"Run project snapshot {key} is invalid")
    return item


def _checksum(value: dict[str, object], key: str) -> str:
    item = _string(value, key, max_length=64)
    if _SHA256_PATTERN.fullmatch(item) is None:
        raise ValueError(f"Run project snapshot {key} is invalid")
    return item


def _require_unique(values: Iterable[Hashable], label: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ValueError(f"Run project snapshot contains duplicate {label}")


def _require_leaf_paths(values: Iterable[str], label: str) -> None:
    paths = sorted((tuple(value.split("/")) for value in values), key=len)
    for index, path in enumerate(paths):
        if any(candidate[: len(path)] == path for candidate in paths[index + 1 :]):
            raise ValueError(f"Run project snapshot contains overlapping {label}")
