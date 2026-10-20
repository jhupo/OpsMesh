"""Skill package contracts: SKILL.md frontmatter, body and resource index.

A skill definition is imported from a standard package: the SKILL.md
frontmatter and body become the configuration and every other file
becomes a declared resource with its digest. Scripts inside a skill are
resources — reading SKILL.md never grants script execution.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from opsmesh.capabilities.packages import package_digest
from opsmesh.capabilities.validation import validate_parameter_schema
from opsmesh.shared.errors import InvalidArgumentError

MAX_FRONTMATTER_BYTES = 65_536
MAX_INSTRUCTION_LENGTH = 100_000
MAX_RESOURCE_ENTRIES = 512
SKILL_MARKDOWN = "SKILL.md"


class SkillFrontmatter(BaseModel):
    """The standard SKILL.md metadata block."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    license: str | None = Field(default=None, max_length=100)


class SkillConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontmatter: SkillFrontmatter
    instructions: str = Field(min_length=1)
    parameters_schema: dict[str, object] = Field(default_factory=dict)

    @field_validator("instructions")
    @classmethod
    def _instructions(cls, value: str) -> str:
        if len(value) > MAX_INSTRUCTION_LENGTH:
            raise ValueError("the SKILL.md body is too long")
        return value

    @field_validator("parameters_schema")
    @classmethod
    def _parameters_schema(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_parameter_schema(value)


def validated_configuration(configuration: object) -> dict[str, object]:
    """Validate one skill configuration, mapping failures to the domain error."""
    try:
        model = SkillConfiguration.model_validate(configuration)
    except ValidationError as exc:
        raise InvalidArgumentError(
            "The skill configuration violates its contract",
            details={"reason": str(exc)},
        ) from exc
    return model.model_dump(mode="json")


def parse_skill_package(
    files: dict[str, bytes],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Derive the skill configuration and resource index from a package.

    Exactly one SKILL.md must exist, either at the package root or inside
    a single top-level directory; every other file becomes a resource
    entry with its digest and size. The caller validates the derived
    configuration through :func:`validated_configuration`.
    """
    markdown_paths = [
        path for path in files if path == SKILL_MARKDOWN or path.endswith(f"/{SKILL_MARKDOWN}")
    ]
    if len(markdown_paths) != 1:
        raise InvalidArgumentError("A skill package must contain exactly one SKILL.md")
    markdown_path = markdown_paths[0]
    prefix = markdown_path[: -len(SKILL_MARKDOWN)]
    if any(not path.startswith(prefix) for path in files):
        raise InvalidArgumentError("A skill package contains files outside its declared root")
    payload = files[markdown_path]
    if len(payload) > MAX_FRONTMATTER_BYTES + MAX_INSTRUCTION_LENGTH:
        raise InvalidArgumentError("The SKILL.md file is too long")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidArgumentError("A SKILL.md file must be UTF-8 text") from exc
    frontmatter, instructions = _split_frontmatter(text)
    resources: list[dict[str, object]] = [
        {
            "path": path,
            "digest": package_digest(files[path]),
            "size": len(files[path]),
            "media_type": _media_type(path),
        }
        for path in sorted(files)
        if path != markdown_path
    ]
    if len(resources) > MAX_RESOURCE_ENTRIES:
        raise InvalidArgumentError("A skill package declares too many resources")
    configuration: dict[str, object] = {
        "frontmatter": frontmatter,
        "instructions": instructions,
        "parameters_schema": {},
    }
    return configuration, resources


def skill_metadata(files: Mapping[str, bytes]) -> tuple[str, str]:
    """The name and description one skill package declares in its SKILL.md."""
    markdown_paths = [
        path for path in files if path == SKILL_MARKDOWN or path.endswith(f"/{SKILL_MARKDOWN}")
    ]
    if len(markdown_paths) != 1:
        raise InvalidArgumentError("A skill package must contain exactly one SKILL.md")
    try:
        text = files[markdown_paths[0]].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidArgumentError("A SKILL.md file must be UTF-8 text") from exc
    frontmatter, _ = _split_frontmatter(text)
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        raise InvalidArgumentError("A SKILL.md frontmatter must declare name and description")
    return name, description


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    if not text.startswith("---"):
        raise InvalidArgumentError("A SKILL.md file must open with a frontmatter block")
    _, _, remainder = text.partition("---\n")
    block, _, body = remainder.partition("\n---")
    if not block.strip():
        raise InvalidArgumentError("A SKILL.md frontmatter block is empty")
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        raise InvalidArgumentError("A SKILL.md frontmatter block is not valid YAML") from exc
    if not isinstance(loaded, dict):
        raise InvalidArgumentError("A SKILL.md frontmatter block must be a mapping")
    try:
        frontmatter = SkillFrontmatter.model_validate(loaded).model_dump(mode="json")
    except ValidationError as exc:
        raise InvalidArgumentError(
            "The SKILL.md frontmatter violates its contract",
            details={"reason": str(exc)},
        ) from exc
    instructions = body.lstrip("\n")
    if not instructions.strip():
        raise InvalidArgumentError("A SKILL.md body must not be empty")
    return frontmatter, instructions


def _media_type(path: str) -> str:
    if re.search(r"\.json$", path):
        return "application/json"
    if re.search(r"\.md$", path):
        return "text/markdown"
    if re.search(r"\.(png|jpe?g|gif|webp)$", path):
        return "image/*"
    return "application/octet-stream"
