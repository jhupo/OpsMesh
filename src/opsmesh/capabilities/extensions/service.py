"""Extension package intake and version coherence.

An extension package carries the code its declared exports resolve to:
intake verifies the declared resources and flow templates against the
package bytes. Nothing is imported, registered or executed — installing
and invoking an extension belongs to the extensions module with an
approved installation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from opsmesh.capabilities.extensions.schemas import validated_configuration
from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.capabilities.validation import (
    PackageIntake,
    verify_declared_resources,
    verify_flow_template_files,
)
from opsmesh.shared.errors import ConflictError


def validate_configuration(configuration: Mapping[str, object]) -> dict[str, object]:
    """Validate one extension configuration through the shared pydantic contract."""
    return validated_configuration(configuration)


def intake_package(
    files: Mapping[str, bytes],
    *,
    configuration: Mapping[str, object],
    resource_manifest: Sequence[Mapping[str, object]],
    flow_templates: Sequence[Mapping[str, object]],
) -> PackageIntake:
    """Verify an extension package against its declared resources."""
    verify_declared_resources(files, resource_manifest)
    verify_flow_template_files(files, flow_templates)
    return PackageIntake(
        configuration=dict(configuration),
        resource_manifest=[dict(entry) for entry in resource_manifest],
        flow_templates=[dict(entry) for entry in flow_templates],
    )


def validate_version(session: Session, version: CapabilityVersion) -> None:
    """An extension version publishes with the package that carries its exports."""
    if version.package_digest is None:
        raise ConflictError("An extension version must publish with its package")


__all__ = ["intake_package", "validate_configuration", "validate_version"]
