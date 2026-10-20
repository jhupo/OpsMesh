"""MCP package intake and version coherence.

A local (stdio) MCP template publishes with its package; a remote one
never carries a package. Intake verifies the declared resources and flow
templates against the package bytes and refuses a transport that
disagrees with the package it was given. Nothing is connected, started
or discovered: running an MCP server belongs to execution.runtime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from opsmesh.capabilities.mcp.schemas import validated_configuration
from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.capabilities.validation import (
    PackageIntake,
    verify_declared_resources,
    verify_flow_template_files,
)
from opsmesh.shared.errors import ConflictError, InvalidArgumentError

MCP_TRANSPORT_REMOTE = "remote"
MCP_TRANSPORT_STDIO = "stdio"


def validate_configuration(configuration: Mapping[str, object]) -> dict[str, object]:
    """Validate one MCP configuration through the shared pydantic contract."""
    return validated_configuration(configuration)


def carries_package(configuration: Mapping[str, object]) -> bool:
    """Whether one MCP configuration publishes with a package.

    A remote template connects to an endpoint and ships nothing; a local
    one launches a command from its package. This is the single rule both
    intake and version coherence enforce.
    """
    return configuration.get("transport") != MCP_TRANSPORT_REMOTE


def intake_package(
    files: Mapping[str, bytes],
    *,
    configuration: Mapping[str, object],
    resource_manifest: Sequence[Mapping[str, object]],
    flow_templates: Sequence[Mapping[str, object]],
) -> PackageIntake:
    """Verify a local MCP package against its declarations.

    A remote template must not carry a package, and a local template's
    declared resources and flow templates must exist in the package with
    their digests. The launch command itself is only declared here; it is
    executed later by the runtime in an approved environment.
    """
    transport = configuration.get("transport")
    if transport == "remote":
        raise InvalidArgumentError("A remote MCP template carries no package")
    if transport != "stdio":
        raise InvalidArgumentError("An MCP transport is unknown")
    verify_declared_resources(files, resource_manifest)
    verify_flow_template_files(files, flow_templates)
    return PackageIntake(
        configuration=dict(configuration),
        resource_manifest=[dict(entry) for entry in resource_manifest],
        flow_templates=[dict(entry) for entry in flow_templates],
    )


def validate_version(session: Session, version: CapabilityVersion) -> None:
    """A local template is published with its package; a remote one without."""
    if carries_package(version.configuration) and version.package_digest is None:
        raise ConflictError("A local MCP version must publish with its package")
    if not carries_package(version.configuration) and version.package_digest is not None:
        raise ConflictError("A remote MCP version must not publish a package")
