"""Build a package from its declared manifest: schema export, digest, archive."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Final

from pydantic import JsonValue

from opsmesh_sdk.manifest import PackageManifest

MANIFEST_NAME: Final = "manifest.json"
SCHEMAS_NAME: Final = "schemas.json"


def export_schemas(manifest: PackageManifest) -> dict[str, JsonValue]:
    """Export every declared input/output schema as one JSON-Schema document."""
    schemas: dict[str, JsonValue] = {}
    for operation in manifest.operations:
        schemas[operation.export_id] = {
            "input": operation.input_schema.model_dump_schema(),
            "output": operation.output_schema.model_dump_schema(),
        }
    for hook in manifest.hooks:
        schemas[hook.export_id] = {"input": hook.input_schema.model_dump_schema()}
    return schemas


def manifest_digest(manifest: PackageManifest) -> str:
    """The stable digest the server pins at installation time."""
    payload = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def build_package(source: Path, target: Path) -> Path:
    """Build the installable archive from a package source directory.

    The archive carries the manifest, the exported schemas and the declared
    source tree. Building never executes package code.
    """
    manifest_path = source / MANIFEST_NAME
    manifest = PackageManifest.model_validate(json.loads(manifest_path.read_text()))
    schemas = export_schemas(manifest)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(MANIFEST_NAME, json.dumps(manifest.model_dump(mode="json"), indent=2))
        archive.writestr(SCHEMAS_NAME, json.dumps(schemas, indent=2))
        for path in sorted(source.rglob("*.py")):
            archive.write(path, path.relative_to(source).as_posix())
    return target


__all__ = ["MANIFEST_NAME", "SCHEMAS_NAME", "build_package", "export_schemas", "manifest_digest"]
