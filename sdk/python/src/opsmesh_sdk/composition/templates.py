"""Read fixed flow templates as data and import them through public authoring APIs."""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from uuid import UUID

from opsmesh_sdk.composition.parameters import normalize
from opsmesh_sdk.errors import ValidationError
from opsmesh_sdk.transport.contracts import GroupChannel

if TYPE_CHECKING:
    from opsmesh_sdk.api.orchestration import DefinitionView

MAX_PACKAGE_BYTES = 32 * 1024 * 1024
MAX_TEMPLATE_BYTES = 1_048_576


def load_template(package: bytes, *, path: str, digest: str) -> dict[str, Any]:
    """Read one bounded, digest-checked JSON member without extracting or importing code."""
    relative = PurePosixPath(path)
    if (
        not path
        or len(path) > 240
        or "\\" in path
        or ":" in path
        or relative.is_absolute()
        or ".." in relative.parts
        or str(relative) != path
        or not path.endswith(".json")
        or not re.fullmatch(r"[a-f0-9]{64}", digest)
    ):
        raise ValidationError("A flow template requires a safe JSON resource path and digest")
    if not package or len(package) > MAX_PACKAGE_BYTES:
        raise ValidationError("The template package is empty or exceeds its byte limit")
    try:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            entries = archive.infolist()
            matches = [entry for entry in entries if entry.filename == path]
            if len(entries) > 1024 or len(matches) != 1:
                raise ValidationError("The package must contain one unique declared template")
            entry = matches[0]
            mode = entry.external_attr >> 16
            if entry.is_dir() or stat.S_ISLNK(mode) or entry.file_size > MAX_TEMPLATE_BYTES:
                raise ValidationError("The template must be a bounded regular resource")
            with archive.open(entry) as stream:
                resource = stream.read(MAX_TEMPLATE_BYTES + 1)
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError) as exc:
        raise ValidationError("The template package is not a readable static archive") from exc
    if len(resource) > MAX_TEMPLATE_BYTES or hashlib.sha256(resource).hexdigest() != digest:
        raise ValidationError("The template resource differs from its published digest")
    try:
        document = json.loads(resource)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValidationError("The flow template is not valid bounded JSON") from exc
    result = normalize(document)
    if not isinstance(result, dict):
        raise ValidationError("The flow template must be a JSON object")
    return result


async def import_template(
    channel: GroupChannel,
    *,
    definition_id: UUID,
    version_number: int,
    path: str,
    name: str,
) -> DefinitionView:
    """Create a user-owned draft from an authorized published capability resource.

    Every download and authoring request uses the current caller's public API
    authorization. The source string is provenance, never an authorization grant.
    """
    from opsmesh_sdk.api.capabilities import create_capabilities_client
    from opsmesh_sdk.api.orchestration import DefinitionCreate, create_orchestration_client

    capabilities = create_capabilities_client(channel)
    version = await capabilities.versions.read(definition_id, version_number)
    if version.status != "published" or not version.digest:
        raise ValidationError("Only a published capability version provides importable templates")
    declaration = next((item for item in version.flow_templates if item.get("path") == path), None)
    digest = declaration.get("digest") if declaration is not None else None
    if not isinstance(digest, str):
        raise ValidationError("The fixed capability version does not declare this template")
    package = await capabilities.versions.package.read(definition_id, version_number)
    spec = load_template(package, path=path, digest=digest)
    spec["template_source"] = f"{version.version_id}:{path}:{digest}"
    request = DefinitionCreate.model_validate({"name": name, "kind": "flow", "spec": spec})
    return await create_orchestration_client(channel).definitions.create(request)
