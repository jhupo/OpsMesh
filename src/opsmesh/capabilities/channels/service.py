"""Channel package intake and version coherence.

A channel package carries its adapter code: intake verifies the declared
resources and flow templates against the package bytes and that every
adapter entrypoint names a file the package declares. Nothing is imported
and no vendor client is constructed — running a channel belongs to the
channels module with an approved installation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from opsmesh.capabilities.channels.schemas import validated_configuration
from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.capabilities.validation import (
    PackageIntake,
    verify_declared_resources,
    verify_flow_template_files,
)
from opsmesh.shared.errors import ConflictError, InvalidArgumentError


def validate_configuration(configuration: Mapping[str, object]) -> dict[str, object]:
    """Validate one channel configuration through the shared pydantic contract."""
    return validated_configuration(configuration)


def intake_package(
    files: Mapping[str, bytes],
    *,
    configuration: Mapping[str, object],
    resource_manifest: Sequence[Mapping[str, object]],
    flow_templates: Sequence[Mapping[str, object]],
) -> PackageIntake:
    """Verify a channel package against its declarations and its entrypoints.

    Every adapter entrypoint must name a module the package declares and
    carries, so a package cannot claim code it does not ship. The
    entrypoint stays an identifier: it is resolved by the runtime in an
    approved environment, never at publish time.
    """
    verify_declared_resources(files, resource_manifest)
    verify_flow_template_files(files, flow_templates)
    declared = {str(entry["path"]) for entry in resource_manifest}
    operations = configuration.get("operations")
    if not isinstance(operations, list):
        raise InvalidArgumentError("A channel configuration must declare its operations")
    for raw in operations:
        if not isinstance(raw, Mapping):
            raise InvalidArgumentError("Every channel operation must be an object")
        entrypoint = raw.get("entrypoint")
        if not isinstance(entrypoint, str):
            raise InvalidArgumentError("A channel entrypoint must name module:function")
        module = entrypoint.split(":", 1)[0]
        module_files = {f"{module.replace('.', '/')}.py", f"{module.replace('.', '/')}/__init__.py"}
        if not module_files & declared:
            raise InvalidArgumentError(
                "A channel entrypoint must name a module the package declares",
                details={"entrypoint": entrypoint},
            )
    return PackageIntake(
        configuration=dict(configuration),
        resource_manifest=[dict(entry) for entry in resource_manifest],
        flow_templates=[dict(entry) for entry in flow_templates],
    )


def validate_version(session: Session, version: CapabilityVersion) -> None:
    """A channel version publishes with the package that carries its adapter."""
    if version.package_digest is None:
        raise ConflictError("A channel version must publish with its package")


__all__ = ["intake_package", "validate_configuration", "validate_version"]
