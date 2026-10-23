"""Build native workspace declarations and capabilities without opening a sandbox."""

from collections.abc import Callable
from dataclasses import dataclass

from agents.sandbox import Manifest
from agents.sandbox.capabilities import Capability, Compaction, Filesystem, Memory, Shell
from agents.sandbox.entries import BaseEntry

from opsmesh.agents.callbacks import CallbackRegistry
from opsmesh.agents.config.capabilities import SandboxCapabilityConfig
from opsmesh.agents.config.manifest import (
    DEFAULT_MEMORY_PATH,
    DEFAULT_ROLLOUTS_PATH,
    EntryRef,
    ManifestConfig,
)
from opsmesh.agents.skills import SkillCatalog, SkillInstallation, prepare_skills
from opsmesh.agents.skills.preparation import EntryResolver

ManifestResolver = Callable[[str], Manifest]


def resolve_manifest(
    config: ManifestConfig | None,
    *,
    entry_resolver: EntryResolver,
    template_resolver: ManifestResolver | None,
) -> Manifest | None:
    if config is None:
        return None
    if config.template_id is not None:
        if template_resolver is None:
            raise ValueError("Manifest template requires an authorized template resolver")
        manifest = template_resolver(config.template_id).model_copy(deep=True)
        if "root" in config.model_fields_set and config.root != manifest.root:
            raise ValueError("A manifest template root cannot be overridden")
    else:
        manifest = Manifest(root=config.root)
    for destination, entry in config.entries.items():
        if isinstance(entry, EntryRef):
            native = entry_resolver(entry).model_copy(deep=True)
        else:
            native = inline_entry(entry)
        if destination in manifest.entries:
            raise ValueError(f"Manifest destination already exists: {destination}")
        manifest.entries[destination] = native
    manifest.environment.value.update(config.environment)
    if config.users:
        manifest.users = [user.model_copy(deep=True) for user in config.users]
    if config.groups:
        manifest.groups = [group.model_copy(deep=True) for group in config.groups]
    manifest.validated_entries()
    return manifest


def inline_entry(value: object) -> BaseEntry:
    from agents.sandbox.entries import Dir

    entry = BaseEntry.parse(value)
    if entry.type not in {"file", "dir", "git_repo"}:
        raise ValueError("Host files and mounts require an authorized EntryRef")
    if isinstance(entry, Dir):
        for child in entry.children.values():
            inline_entry(child)
    return entry


@dataclass(frozen=True)
class PreparedCapabilities:
    capabilities: list[Capability]
    installations: tuple[SkillInstallation, ...]


def resolve_capabilities(
    configs: list[SandboxCapabilityConfig],
    *,
    skills: SkillCatalog,
    callbacks: CallbackRegistry,
    entry_resolver: EntryResolver,
) -> PreparedCapabilities:
    capabilities: list[Capability] = []
    installations: list[SkillInstallation] = []
    native_types = {
        "filesystem": Filesystem,
        "shell": Shell,
        "memory": Memory,
        "compaction": Compaction,
    }
    for config in configs:
        if config.type == "skills":
            prepared = prepare_skills(
                config, catalog=skills, callbacks=callbacks, resolve_entry=entry_resolver
            )
            capability: Capability = prepared.capability
            installations.extend(prepared.installations)
        elif config.factory is not None:
            capability = callbacks.resolve("capability", config.factory)
            if not isinstance(capability, Capability):
                raise TypeError("capability factory must return a native Capability")
            capability = capability.clone()
        else:
            native_type = native_types[config.type]
            unknown = config.options.keys() - native_type.model_fields.keys()
            if unknown:
                raise ValueError(f"Unknown {config.type} options: {sorted(unknown)}")
            options = config.options.copy()
            if config.type == "memory":
                layout = options.get("layout", {})
                if isinstance(layout, dict):
                    options["layout"] = {
                        "memories_dir": DEFAULT_MEMORY_PATH,
                        "sessions_dir": DEFAULT_ROLLOUTS_PATH,
                        **layout,
                    }
            capability = native_type.model_validate(options)
            if config.configure_tools is not None:
                configure = callbacks.resolve("configure_tools", config.configure_tools)
                if not callable(configure):
                    raise TypeError("configure_tools factory must return a callable")
                capability = capability.model_copy(update={"configure_tools": configure})
        capabilities.append(capability)
    types = {capability.type for capability in capabilities}
    if len(types) != len(capabilities):
        raise ValueError("Sandbox capability types must be unique")
    for capability in capabilities:
        missing = capability.required_capability_types() - types
        if missing:
            raise ValueError(f"{capability.type} requires capabilities: {sorted(missing)}")
    return PreparedCapabilities(capabilities, tuple(installations))
