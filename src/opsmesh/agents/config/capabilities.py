"""Serializable selectors for the native sandbox capabilities."""

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .base import ConfigValue
from .callbacks import CallbackRef
from .manifest import DEFAULT_SKILLS_PATH, EntryRef, relative_path


class SandboxCapabilityConfig(ConfigValue):
    type: Literal["filesystem", "shell", "skills", "memory", "compaction", "registered"]
    options: dict[str, JsonValue] = Field(default_factory=dict)
    skill_ids: list[str] = Field(default_factory=list)
    skills_path: str = DEFAULT_SKILLS_PATH
    from_: EntryRef | None = None
    lazy_from: CallbackRef | None = None
    factory: CallbackRef | None = None
    configure_tools: CallbackRef | None = None

    @model_validator(mode="after")
    def validate_sources(self) -> "SandboxCapabilityConfig":
        if self.type == "skills":
            relative_path(self.skills_path)
            if len(set(self.skill_ids)) != len(self.skill_ids):
                raise ValueError("Skill IDs must be unique")
            if sum((bool(self.skill_ids), self.from_ is not None, self.lazy_from is not None)) != 1:
                raise ValueError("Skills requires exactly one source")
            if self.options:
                raise ValueError("Skills uses skill_ids, from_ or lazy_from, not options")
        elif self.skill_ids or self.from_ is not None or self.lazy_from is not None:
            raise ValueError("Skill sources belong only to the skills capability")
        if (self.type == "registered") != (self.factory is not None):
            raise ValueError("Only a registered capability requires factory")
        if self.type == "registered" and self.options:
            raise ValueError("Registered capability options belong to factory.config")
        if self.type != "skills" and self.skills_path != DEFAULT_SKILLS_PATH:
            raise ValueError("skills_path belongs only to the skills capability")
        if self.configure_tools is not None and self.type not in {"filesystem", "shell"}:
            raise ValueError("configure_tools belongs only to filesystem and shell")
        if {"session", "run_as", "workspace_scope", "configure_tools"} & self.options.keys():
            raise ValueError("Runtime bindings cannot be configured through capability options")
        return self
