"""Prepare one shared workspace declaration before the SDK resumes a borrowed session."""

from pathlib import Path, PurePosixPath

from agents.sandbox import Manifest, SandboxAgent
from agents.sandbox.capabilities import Skills
from agents.sandbox.entries import BaseEntry, Dir, Mount

from opsmesh.agents.assembly import AgentBuild
from opsmesh.execution.runtime.schemas import SANDBOX_WORKDIR


def _merge_entry(previous: BaseEntry, incoming: BaseEntry, path: str) -> BaseEntry:
    if previous == incoming:
        return previous
    if (
        not isinstance(previous, Dir)
        or not isinstance(incoming, Dir)
        or previous.model_dump(exclude={"children"}) != incoming.model_dump(exclude={"children"})
    ):
        raise ValueError(f"Conflicting shared workspace entries: {path}")
    children = dict(previous.children)
    for name, entry in incoming.children.items():
        children[name] = (
            _merge_entry(children[name], entry, f"{path}/{name}")
            if name in children
            else entry.model_copy(deep=True)
        )
    return previous.model_copy(update={"children": children})


def agent_manifest(built: AgentBuild) -> Manifest:
    manifest = Manifest(root=SANDBOX_WORKDIR)
    entries: dict[str | Path, BaseEntry] = {}
    lazy_roots: set[str] = set()
    static_roots: set[str] = set()

    def install(destination: str | Path, entry: BaseEntry) -> None:
        path = PurePosixPath(str(destination).replace("\\", "/")).as_posix()
        entries[path] = (
            _merge_entry(entries[path], entry, path)
            if path in entries
            else entry.model_copy(deep=True)
        )

    for agent in built.agents.values():
        if not isinstance(agent, SandboxAgent):
            continue
        source = agent.default_manifest
        if source is not None:
            if source.root != SANDBOX_WORKDIR or source.extra_path_grants:
                raise ValueError("Borrowed environments use only their authorized /workspace mount")
            if source.users or source.groups:
                raise ValueError("Borrowed environments require users/groups in the compute image")
            for destination, entry in source.entries.items():
                install(destination, entry)
            for name, value in source.environment.value.items():
                if name in manifest.environment.value and manifest.environment.value[name] != value:
                    raise ValueError(f"Conflicting shared workspace environment: {name}")
                manifest.environment.value[name] = value
        for capability in agent.capabilities:
            if isinstance(capability, Skills):
                if capability.lazy_from is not None:
                    lazy_roots.add(capability.skills_path)
                elif capability.from_ is not None:
                    # Static installations below supply every selected source in the
                    # shared graph. An explicit root prevents SDK from_ replacement.
                    static_roots.add(capability.skills_path)
    for installations in built.skill_installations.values():
        for installation in installations:
            install(installation.destination, installation.entry)
    for root in static_roots:
        entries.setdefault(root, Dir())
    for root in lazy_roots:
        placeholder = entries.get(root)
        if isinstance(placeholder, Dir) and not placeholder.children:
            del entries[root]
        for path in entries:
            candidate, reserved = PurePosixPath(str(path)), PurePosixPath(root)
            if (
                candidate == reserved
                or candidate in reserved.parents
                or reserved in candidate.parents
            ):
                raise ValueError(f"Lazy skills overlap shared workspace entries: {root}")
    manifest.entries = entries
    declared: dict[Path, BaseEntry] = {}
    for path, entry in manifest.iter_entries():
        if isinstance(entry, Mount):
            raise ValueError("A borrowed environment cannot acquire additional mounts")
        if path in declared and declared[path] != entry:
            raise ValueError(f"Conflicting shared workspace entries: {path}")
        declared[path] = entry
    manifest.validated_entries()
    return manifest
