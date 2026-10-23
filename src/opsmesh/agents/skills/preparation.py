"""Build native Skills and expose installation requirements for persistent environments."""

from collections.abc import Callable
from dataclasses import dataclass

from agents.sandbox.capabilities import LazySkillSource, Skill, Skills
from agents.sandbox.entries import BaseEntry, Dir

from opsmesh.agents.callbacks import CallbackRegistry
from opsmesh.agents.config.capabilities import SandboxCapabilityConfig
from opsmesh.agents.config.manifest import EntryRef

from .catalog import SkillCatalog

EntryResolver = Callable[[EntryRef], BaseEntry]


@dataclass(frozen=True)
class SkillInstallation:
    destination: str
    entry: BaseEntry


@dataclass(frozen=True)
class PreparedSkills:
    capability: Skills
    installations: tuple[SkillInstallation, ...]


def prepare_skills(
    config: SandboxCapabilityConfig,
    *,
    catalog: SkillCatalog,
    callbacks: CallbackRegistry,
    resolve_entry: EntryResolver,
) -> PreparedSkills:
    if config.type != "skills":
        raise ValueError("Expected a skills capability")
    if config.from_ is not None:
        source = resolve_entry(config.from_).model_copy(deep=True)
        capability = Skills(from_=source, skills_path=config.skills_path)
        return PreparedSkills(capability, (SkillInstallation(config.skills_path, source),))
    if config.lazy_from is not None:
        lazy_source = callbacks.resolve("skill_source", config.lazy_from)
        if not isinstance(lazy_source, LazySkillSource):
            raise TypeError("skill_source factory must return a native LazySkillSource")
        return PreparedSkills(Skills(lazy_from=lazy_source, skills_path=config.skills_path), ())
    skills: list[Skill] = []
    directories: dict[str, BaseEntry] = {}
    selected = [catalog.get(skill_id) for skill_id in config.skill_ids]
    if any(info.directory is not None for info in selected) and any(
        info.deferred for info in selected
    ):
        raise ValueError("Deferred skills cannot be combined with directory skills")
    for info in selected:
        if info.name in directories:
            raise ValueError(f"Selected skills have the same installation name: {info.name}")
        if info.directory is not None:
            directory = resolve_entry(info.directory).model_copy(deep=True)
            if not directory.is_dir:
                raise ValueError("A skill directory reference must resolve to a directory")
            directories[info.name] = directory
            continue
        if info.content is None:
            raise ValueError("A skill requires content")
        skills.append(
            Skill(
                name=info.name,
                description=info.description,
                content=resolve_entry(info.content).model_copy(deep=True)
                if isinstance(info.content, EntryRef)
                else info.content,
                compatibility=info.compatibility,
                deferred=info.deferred,
                **{
                    group: {
                        name: resolve_entry(reference).model_copy(deep=True)
                        for name, reference in getattr(info, group).items()
                    }
                    for group in ("scripts", "references", "assets")
                },
            )
        )
        directories[info.name] = skills[-1].as_dir_entry()
    capability = (
        Skills(from_=Dir(children=directories), skills_path=config.skills_path)
        if any(info.directory is not None for info in selected)
        else Skills(skills=skills, skills_path=config.skills_path)
    )
    return PreparedSkills(
        capability,
        tuple(
            SkillInstallation(f"{config.skills_path}/{name}", entry)
            for name, entry in directories.items()
        ),
    )
