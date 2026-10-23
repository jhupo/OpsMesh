"""Skill metadata and content references, with no package execution or filesystem I/O."""

from pydantic import Field, field_validator, model_validator

from opsmesh.agents.config.base import ConfigValue, Identifier
from opsmesh.agents.config.manifest import EntryRef, relative_path


class SkillInfo(ConfigValue):
    id: Identifier
    name: str
    description: str
    source: str = "system"
    content: str | EntryRef | None = Field(default=None, repr=False)
    directory: EntryRef | None = None
    compatibility: str | None = None
    scripts: dict[str, EntryRef] = Field(default_factory=dict)
    references: dict[str, EntryRef] = Field(default_factory=dict)
    assets: dict[str, EntryRef] = Field(default_factory=dict)
    deferred: bool = False

    @model_validator(mode="after")
    def validate_source(self) -> "SkillInfo":
        if (self.content is not None) == (self.directory is not None):
            raise ValueError("A skill requires either content or a complete directory reference")
        if self.directory is not None and (
            self.scripts or self.references or self.assets or self.deferred
        ):
            raise ValueError("Directory skills already contain their files and cannot be deferred")
        return self

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return relative_path(value)

    @field_validator("scripts", "references", "assets")
    @classmethod
    def validate_paths(cls, value: dict[str, EntryRef]) -> dict[str, EntryRef]:
        for destination in value:
            relative_path(destination)
        return value


class SkillCatalog:
    def __init__(self) -> None:
        self._skills: dict[str, SkillInfo] = {}

    def register(self, info: SkillInfo, *, replace: bool = False) -> None:
        if info.id in self._skills and not replace:
            raise ValueError(f"Skill already registered: {info.id}")
        self._skills[info.id] = SkillInfo.model_validate(info.model_dump())

    def get(self, skill_id: str) -> SkillInfo:
        return self._skills[skill_id].model_copy(deep=True)

    def list(self) -> list[SkillInfo]:
        return [self.get(identifier) for identifier in sorted(self._skills)]

    def remove(self, skill_id: str) -> None:
        del self._skills[skill_id]
