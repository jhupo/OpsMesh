"""Octop-shaped identity; reading a manifest never imports plugin code."""

from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:rc[0-9]+)?$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    icon: str = Field(default="", max_length=200)
    kind: Literal[
        "tool", "skill", "mcp", "hook", "middleware", "channel", "message_filter", "plugin"
    ]
    entry: str = "main.py"

    @field_validator("entry")
    @classmethod
    def entry_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or str(path) != value
            or ".." in path.parts
            or "\\" in value
            or ":" in value
            or path.suffix != ".py"
            or any(not part.isidentifier() for part in path.with_suffix("").parts)
        ):
            raise ValueError("entry must be a relative Python source path")
        return value


def parse_manifest(content: bytes) -> PluginManifest:
    if len(content) > 64_000:
        raise ValueError("Plugin manifest exceeds 64 KB")
    return PluginManifest.model_validate(yaml.safe_load(content))


def read_manifest(directory: Path) -> PluginManifest:
    return parse_manifest((directory / "plugin.yaml").read_bytes())
