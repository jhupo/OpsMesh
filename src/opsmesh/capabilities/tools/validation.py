"""Tool package intake and version coherence.

A script tool publishes with the package that carries its entrypoint; an
SDK export tool may publish with or without a package. Intake checks the
entrypoint against the package statically and refuses the direct
execution mode outside trusted system registration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.capabilities.tools.schemas import validated_configuration
from opsmesh.capabilities.validation import (
    PackageIntake,
    verify_declared_resources,
    verify_flow_template_files,
)
from opsmesh.shared.errors import ConflictError, InvalidArgumentError


def validate_configuration(configuration: Mapping[str, object]) -> dict[str, object]:
    """Validate one tool configuration through the shared pydantic contract."""
    return validated_configuration(configuration)


def intake_package(
    files: Mapping[str, bytes],
    *,
    configuration: Mapping[str, object],
    resource_manifest: Sequence[Mapping[str, object]],
    flow_templates: Sequence[Mapping[str, object]],
) -> PackageIntake:
    """Verify a tool package against its declarations and its entrypoint.

    A script entrypoint must be one of the declared resources, so a
    package cannot claim a file it does not carry. Nothing is imported or
    executed: the entrypoint is a name, checked statically.
    """
    verify_declared_resources(files, resource_manifest)
    verify_flow_template_files(files, flow_templates)
    execution_mode = configuration.get("execution_mode")
    entrypoint = configuration.get("entrypoint")
    if execution_mode == "script":
        if not isinstance(entrypoint, str) or entrypoint not in files:
            raise InvalidArgumentError(
                "A script tool entrypoint must be a file the package declares",
                details={"entrypoint": entrypoint},
            )
        declared = {str(entry["path"]) for entry in resource_manifest}
        if entrypoint not in declared:
            raise InvalidArgumentError(
                "A script tool entrypoint must appear in the resource manifest",
                details={"entrypoint": entrypoint},
            )
    elif execution_mode != "sdk_export":
        raise InvalidArgumentError(
            "A tool execution mode is one of the user-declared modes; "
            "direct execution is reserved for trusted system registration"
        )
    return PackageIntake(
        configuration=dict(configuration),
        resource_manifest=[dict(entry) for entry in resource_manifest],
        flow_templates=[dict(entry) for entry in flow_templates],
    )


def validate_version(session: Session, version: CapabilityVersion) -> None:
    """A script tool publishes with its package.

    The direct execution mode is a system-registration fact checked by the
    lifecycle, which owns the definition source; the type service only
    knows the version content.
    """
    execution_mode = version.configuration.get("execution_mode")
    if execution_mode == "script" and version.package_digest is None:
        raise ConflictError("A script tool version must publish with its package")
