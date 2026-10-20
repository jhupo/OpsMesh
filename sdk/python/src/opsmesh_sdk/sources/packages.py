"""Verify package bytes and organize catalog import through the generated API."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Literal

from opsmesh_sdk.errors import ValidationError
from opsmesh_sdk.transport.contracts import GroupChannel

if TYPE_CHECKING:
    from opsmesh_sdk.api.capabilities import CapabilityVersionView
    from opsmesh_sdk.sources.github import ReleasePackage

MAX_PACKAGE_BYTES = 32 * 1024 * 1024


def verify_package(payload: bytes, expected_digest: str) -> None:
    if not payload or len(payload) > MAX_PACKAGE_BYTES:
        raise ValidationError("The package is empty or exceeds the size limit")
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise ValidationError("The package digest differs from the release manifest")


async def import_package(
    channel: GroupChannel,
    declaration: ReleasePackage,
    payload: bytes,
    *,
    source: Literal["user", "system"] = "user",
    publish: bool = True,
) -> CapabilityVersionView:
    """Import a verified package; system attribution remains administrator-authorized.

    A rejected attachment or publish leaves a visible draft for correction. Publishing
    records a static version; executable installation is a separate administrator API.
    """
    from opsmesh_sdk.api.capabilities import DefinitionInput, create_capabilities_client

    verify_package(payload, declaration.sha256)
    client = create_capabilities_client(channel)
    request = DefinitionInput.model_validate(
        {
            "key": declaration.key,
            "name": declaration.name,
            "description": declaration.description,
            "visibility": declaration.visibility,
            "source": source,
            "declaration": {"type": declaration.type, "configuration": declaration.configuration},
            "resource_manifest": declaration.resource_manifest,
            "dependencies": declaration.dependencies,
            "flow_templates": declaration.flow_templates,
        }
    )
    version = await client.definitions.create(request)
    version = await client.definitions.package.attach(
        version.definition_id,
        payload,
        filename=declaration.asset,
        content_type="application/octet-stream",
    )
    if publish:
        version = await client.versions.publish(version.definition_id, version.version)
    return version
