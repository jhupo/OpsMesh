"""Skill intake: the package is the configuration.

A skill version is imported from its package: the derived configuration
and resource index come from the SKILL.md and the remaining files. The
service refuses a publish whose package no longer matches what the draft
was imported from, and never executes anything inside the package.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy.orm import Session

from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.capabilities.skills.schemas import parse_skill_package, validated_configuration
from opsmesh.capabilities.validation import PackageIntake, verify_flow_template_files
from opsmesh.shared.errors import ConflictError


def validate_configuration(configuration: Mapping[str, object]) -> dict[str, object]:
    """Validate one skill configuration through the shared pydantic contract."""
    return validated_configuration(configuration)


def intake_package(
    files: Mapping[str, bytes],
    *,
    configuration: Mapping[str, object],
    resource_manifest: Sequence[Mapping[str, object]],
    flow_templates: Sequence[Mapping[str, object]],
) -> PackageIntake:
    """Derive the skill configuration and resource index from the package.

    Any configuration a caller declared is replaced by what the package
    says: the SKILL.md frontmatter and body are the skill. Declared flow
    templates must exist in the package with their digests.
    """
    derived, resources = parse_skill_package(dict(files))
    validated = validated_configuration(derived)
    verify_flow_template_files(files, flow_templates)
    return PackageIntake(
        configuration=validated,
        resource_manifest=resources,
        flow_templates=[dict(entry) for entry in flow_templates],
    )


def validate_version(session: Session, version: CapabilityVersion) -> None:
    """A skill version is published with the package it was imported from."""
    if version.package_digest is None:
        raise ConflictError("A skill version must publish with its package")
