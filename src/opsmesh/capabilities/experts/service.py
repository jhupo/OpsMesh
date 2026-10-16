"""Expert configuration validation and version coherence.

The expert service owns the type contract and the publish-time coherence
between an expert's declared capability slots and its fixed dependencies.
It stores no execution state and never imports expert code: an expert
definition is versioned content, and running one belongs to execution.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from opsmesh.capabilities.experts.schemas import validated_configuration
from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.capabilities.validation import (
    PackageIntake,
    verify_declared_resources,
    verify_flow_template_files,
)
from opsmesh.shared.errors import ConflictError


def validate_configuration(configuration: Mapping[str, object]) -> dict[str, object]:
    """Validate one expert configuration through the shared pydantic contract."""
    return validated_configuration(configuration)


def intake_package(
    files: Mapping[str, bytes],
    *,
    configuration: Mapping[str, object],
    resource_manifest: Sequence[Mapping[str, object]],
    flow_templates: Sequence[Mapping[str, object]],
) -> PackageIntake:
    """Verify the resources an expert package declares.

    An expert's instructions live in the configuration itself; a package
    only carries declared resources (a versioned AGENTS.md, reference
    material), which must exist with their digests. Nothing inside the
    package is imported or executed.
    """
    verify_declared_resources(files, resource_manifest)
    verify_flow_template_files(files, flow_templates)
    return PackageIntake(
        configuration=dict(configuration),
        resource_manifest=[dict(entry) for entry in resource_manifest],
        flow_templates=[dict(entry) for entry in flow_templates],
    )


def validate_version(session: Session, version: CapabilityVersion) -> None:
    """Every fixed dependency must fill a declared slot of its own kind."""
    slots: dict[str, str] = {}
    raw_slots = version.configuration.get("capability_slots")
    if isinstance(raw_slots, list):
        for entry in raw_slots:
            if isinstance(entry, dict):
                slot = entry.get("slot")
                kind = entry.get("kind")
                if isinstance(slot, str) and isinstance(kind, str):
                    slots[slot] = kind
    for dependency in version.dependencies:
        key = dependency.get("key")
        dependency_type = dependency.get("type")
        if not isinstance(key, str) or not isinstance(dependency_type, str):
            raise ConflictError("A fixed dependency is malformed")
        if key not in slots:
            raise ConflictError(
                "A fixed dependency fills no declared capability slot",
                details={"slot": key, "type": dependency_type},
            )
        if slots[key] != dependency_type:
            raise ConflictError(
                "A fixed dependency does not match its slot kind",
                details={"slot": key, "slot_kind": slots[key], "type": dependency_type},
            )
