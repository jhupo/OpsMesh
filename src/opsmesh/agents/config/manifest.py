"""Manifest declarations and references to externally authorized workspace entries."""

from pathlib import PurePosixPath
from typing import Final, Literal

from agents.sandbox import Group, Permissions, User
from pydantic import Field, JsonValue, field_validator

from .base import ConfigValue, Identifier

DEFAULT_SKILLS_PATH: Final = ".opsmesh/skills"
DEFAULT_SESSION_PATH: Final = ".opsmesh/session"
DEFAULT_MEMORY_PATH: Final = ".opsmesh/memory"
DEFAULT_ROLLOUTS_PATH: Final = ".opsmesh/rollouts"


def relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in value
        or ":" in value
        or "\x00" in value
    ):
        raise ValueError("Expected a workspace-relative POSIX path")
    if not path.parts or path.as_posix() != value:
        raise ValueError("Expected a nonempty normalized workspace-relative path")
    return value


class EntryRef(ConfigValue):
    type: Literal["reference"] = "reference"
    entry_id: Identifier


class ManifestConfig(ConfigValue):
    root: str = "/workspace"
    entries: dict[str, dict[str, JsonValue] | EntryRef] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict, repr=False)
    users: list[User] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)
    # Host grants and secret environment values come from this trusted factory,
    # never from JSON supplied as a Manifest declaration.
    template_id: Identifier | None = None

    @field_validator("entries", mode="before")
    @classmethod
    def parse_references(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {
            name: EntryRef.model_validate(entry)
            if isinstance(entry, dict) and entry.get("type") == "reference"
            else entry
            for name, entry in value.items()
        }

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: str) -> str:
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
            raise ValueError("Manifest root must be an absolute POSIX directory")
        return value

    @field_validator("entries")
    @classmethod
    def validate_destinations(
        cls, value: dict[str, dict[str, JsonValue] | EntryRef]
    ) -> dict[str, dict[str, JsonValue] | EntryRef]:
        for destination in value:
            relative_path(destination)
        return value


def default_workspace_manifest() -> ManifestConfig:
    """Declare the OpsMesh layout; materialization belongs to the sandbox client."""
    directories = (
        "files",
        "outputs",
        "tmp",
        DEFAULT_SKILLS_PATH,
        DEFAULT_SESSION_PATH,
        DEFAULT_MEMORY_PATH,
        DEFAULT_ROLLOUTS_PATH,
    )
    return ManifestConfig(
        entries={
            path: {
                "type": "dir",
                "permissions": Permissions.from_mode(0o770).model_dump(mode="json"),
            }
            for path in directories
        }
    )
