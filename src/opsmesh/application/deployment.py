"""Declarative application publication through authorized public platform services.

Apply before starting message consumers and workers. Publication is a resumable
sequence of audited, individually atomic operations, not a cross-service database
transaction. Definitions are explicit: deployment never creates users, invents
permission grants, executes source files, or removes undeclared resources.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import AccessRole, AccountManagement, PermissionGrant
from opsmesh.modules.agents.management import AgentManagement
from opsmesh.modules.bots.api import BotAccount, BotAccounts, BotRoute
from opsmesh.modules.capabilities.api import SkillDefinition, ToolDefinition
from opsmesh.modules.capabilities.knowledge import KnowledgeCollection
from opsmesh.modules.capabilities.knowledge_management import KnowledgeManagement
from opsmesh.modules.capabilities.management import CapabilityManagement
from opsmesh.modules.capabilities.skill_management import SkillManagement
from opsmesh.modules.memory.api import MemoryNamespace, MemoryNamespaceSettings
from opsmesh.modules.memory.management import MemoryManagement
from opsmesh.modules.models.api import ModelProfile
from opsmesh.modules.models.management import ModelManagement
from opsmesh.modules.workspaces.management import WorkspaceManagement


@dataclass(frozen=True, kw_only=True)
class WorkspaceDefinition:
    id: str
    source: Path | None = None
    managed_paths: tuple[str, ...] = ()
    text_substitutions: dict[str, str] = field(default_factory=dict)
    protected_paths: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class SkillSourceDefinition:
    name: str
    source: Path
    version: str = "1"
    description: str = ""

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True, kw_only=True)
class MemoryNamespaceDefinition:
    id: str
    settings: MemoryNamespaceSettings = field(default_factory=MemoryNamespaceSettings)


@dataclass(frozen=True, kw_only=True)
class ApplicationDefinition:
    models: tuple[ModelProfile, ...] = ()
    tools: tuple[ToolDefinition, ...] = ()
    workspaces: tuple[WorkspaceDefinition, ...] = ()
    skills: tuple[SkillSourceDefinition, ...] = ()
    agents: tuple[AgentSpecification, ...] = ()
    roles: tuple[AccessRole, ...] = ()
    bot_accounts: tuple[BotAccount, ...] = ()
    routes: tuple[BotRoute, ...] = ()
    knowledge_collections: tuple[KnowledgeCollection, ...] = ()
    memory_namespaces: tuple[MemoryNamespaceDefinition, ...] = ()


@dataclass(frozen=True, kw_only=True)
class DeploymentServices:
    """Explicit public, authorized service ports supplied by the composition root."""

    accounts: AccountManagement
    agents: AgentManagement
    models: ModelManagement
    capabilities: CapabilityManagement
    skills: SkillManagement
    workspaces: WorkspaceManagement
    bots: BotAccounts
    knowledge: KnowledgeManagement
    memory: MemoryManagement


@dataclass(frozen=True, kw_only=True)
class DeploymentResult:
    # Keys include their resource kind, for example "agent:assistant@1".
    references: dict[str, str] = field(default_factory=dict)
    agents: dict[str, str] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)
    skills: dict[str, str] = field(default_factory=dict)
    workspaces: tuple[str, ...] = ()


def _path(value: str) -> str:
    candidate = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise OpsMeshError("PATH_INVALID", "Deployment paths must be relative POSIX paths")
    return value


def _within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _source_files(root: Path, selected: tuple[str, ...]) -> dict[str, bytes]:
    if root.is_symlink() or not root.is_dir():
        raise OpsMeshError("PATH_INVALID", "Application source must be a trusted directory")
    root = root.resolve()
    files: dict[str, bytes] = {}
    for name in selected:
        target = root.joinpath(*PurePosixPath(_path(name)).parts)
        for parent in (target, *target.parents):
            if parent == root.parent:
                break
            if parent.is_symlink():
                raise OpsMeshError("PATH_INVALID", "Deployment sources cannot contain symlinks")
        if not target.exists():
            raise OpsMeshError("NOT_FOUND", "Declared deployment source does not exist")
        paths = sorted(target.rglob("*")) if target.is_dir() else [target]
        for path in paths:
            relative = path.relative_to(root)
            if any(part in {"__pycache__", ".git"} for part in relative.parts) or path.suffix in {
                ".pyc",
                ".pyo",
            }:
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise OpsMeshError("PATH_INVALID", "Deployment source escapes its directory")
            if path.is_dir():
                continue
            if not path.is_file():
                raise OpsMeshError("PATH_INVALID", "Deployment source must contain regular files")
            if path.stat().st_size > 16 * 1024 * 1024:
                raise OpsMeshError("RESOURCE_EXHAUSTED", "Deployment file exceeds 16 MiB")
            files[_path(relative.as_posix())] = path.read_bytes()
            if len(files) > 4096 or sum(len(data) for data in files.values()) > 64 * 1024 * 1024:
                raise OpsMeshError(
                    "RESOURCE_EXHAUSTED", "Deployment source exceeds its file budget"
                )
    return files


def _same(left: Any, right: Any) -> bool:
    return bool(left.model_dump(exclude={"revision"}) == right.model_dump(exclude={"revision"}))


def _unique(values: tuple[Any, ...], attribute: str, kind: str) -> None:
    identifiers = [getattr(value, attribute) for value in values]
    if len(set(identifiers)) != len(identifiers):
        raise OpsMeshError("CONFIG_INVALID", f"Duplicate {kind} in application definition")


class OpsMeshApplicationDeployment:
    def __init__(self, services: DeploymentServices) -> None:
        self.services = services

    async def apply(self, definition: ApplicationDefinition, *, actor_id: str) -> DeploymentResult:
        """Create or update declared resources, retaining stable IDs and learned files."""
        sources, skill_sources = self._validate(definition)
        references: dict[str, str] = {}
        for collection in definition.knowledge_collections:
            await self._knowledge(collection, actor_id)
        models: dict[str, str] = {}
        skills: dict[str, str] = {}
        agents: dict[str, str] = {}
        for model in definition.models:
            actual = await self._model(model, actor_id)
            models[model.id] = actual
            references[f"model:{model.id}@{model.revision}"] = actual
        for namespace in definition.memory_namespaces:
            policy = namespace.settings.policy
            profiles = {
                name: references.get(f"model:{reference}", models.get(reference, reference))
                for name in ("extract_model_profile", "consolidate_model_profile")
                if (reference := getattr(policy, name)) is not None
            }
            settings = namespace.settings.model_copy(
                update={"policy": policy.model_copy(update=profiles)}
            )
            await self._memory(replace(namespace, settings=settings), actor_id)
        for tool in definition.tools:
            await self._tool(tool, actor_id)
            references[f"tool:{tool.ref}"] = tool.ref
        for workspace in definition.workspaces:
            await self._workspace(workspace, sources[workspace.id], actor_id)
        for skill in definition.skills:
            published = await self._skill(skill, skill_sources[skill.ref], actor_id)
            skills[skill.ref] = published.ref
            references[f"skill:{skill.ref}"] = published.ref
        for specification in self._agent_order(definition.agents):
            updated = self._agent_references(specification, references, models, agents)
            await self._skill_files(updated, definition, skills, skill_sources, actor_id)
            actual = await self._agent(updated, actor_id)
            agents[specification.id] = actual
            references[f"agent:{specification.ref}"] = actual
        for role in definition.roles:
            grants = tuple(self._grant(item, references, models, agents) for item in role.grants)
            await self._role(role.model_copy(update={"grants": grants}), actor_id)
        for account in definition.bot_accounts:
            await self._bot(account, actor_id)
        for route in definition.routes:
            target = references.get(f"agent:{route.agent_revision}", route.agent_revision)
            target = agents.get(target, target)
            await self._route(route.model_copy(update={"agent_revision": target}), actor_id)
        return DeploymentResult(
            references=references,
            agents=agents,
            models=models,
            skills=skills,
            workspaces=tuple(item.id for item in definition.workspaces),
        )

    @staticmethod
    def _validate(
        definition: ApplicationDefinition,
    ) -> tuple[dict[str, dict[str, bytes]], dict[str, dict[str, bytes]]]:
        for values, attribute, kind in (
            (definition.models, "id", "model"),
            (definition.tools, "ref", "tool"),
            (definition.workspaces, "id", "workspace"),
            (definition.skills, "name", "skill"),
            (definition.agents, "id", "agent"),
            (definition.roles, "name", "role"),
            (definition.bot_accounts, "id", "bot account"),
            (definition.knowledge_collections, "id", "knowledge collection"),
            (definition.memory_namespaces, "id", "memory namespace"),
        ):
            _unique(values, attribute, kind)
        route_ids = [(item.bot_id, item.channel_id) for item in definition.routes]
        if len(set(route_ids)) != len(route_ids):
            raise OpsMeshError("CONFIG_INVALID", "Duplicate bot route in application definition")
        result = {}
        for workspace in definition.workspaces:
            protected = tuple(_path(path) for path in workspace.protected_paths)
            if workspace.source is None and workspace.managed_paths:
                raise OpsMeshError("CONFIG_INVALID", "Managed paths require a workspace source")
            content = (
                _source_files(workspace.source, workspace.managed_paths)
                if workspace.source is not None
                else {}
            )
            if any(_within(path, prefix) for path in content for prefix in protected):
                raise OpsMeshError("CONFIG_INVALID", "Deployment includes a protected data file")
            if workspace.text_substitutions:
                for path, data in content.items():
                    try:
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    for old, new in workspace.text_substitutions.items():
                        if not old:
                            raise OpsMeshError(
                                "CONFIG_INVALID", "Template substitution key is empty"
                            )
                        text = text.replace(old, new)
                    content[path] = text.encode("utf-8")
            result[workspace.id] = content
        skill_sources = {}
        for skill in definition.skills:
            if skill.source.is_symlink() or not skill.source.is_dir():
                raise OpsMeshError("PATH_INVALID", "Skill source must be a trusted directory")
            files = _source_files(skill.source, tuple(path.name for path in skill.source.iterdir()))
            if "SKILL.md" not in files:
                raise OpsMeshError("CONFIG_INVALID", "Skill source requires SKILL.md")
            skill_sources[skill.ref] = files
        OpsMeshApplicationDeployment._agent_order(definition.agents)
        return result, skill_sources

    async def _knowledge(self, requested: KnowledgeCollection, actor: str) -> None:
        if requested.deleted:
            raise OpsMeshError("CONFIG_INVALID", "Cannot declare a deleted knowledge collection")
        try:
            current = await self.services.knowledge.get_collection(requested.id, principal_id=actor)
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
            await self.services.knowledge.create_collection(requested, principal_id=actor)
        else:
            if current.deleted:
                raise OpsMeshError("CONFLICT", "Knowledge collection is deleted")
            if not _same(current, requested):
                await self.services.knowledge.update_collection(
                    requested, principal_id=actor, expected_revision=current.revision
                )

    async def _model(self, requested: ModelProfile, actor: str) -> str:
        catalog = self.services.models
        try:
            current = await catalog.get(requested.id, actor_id=actor)
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
            current = await catalog.create(
                requested.model_copy(update={"revision": 1}), actor_id=actor
            )
        else:
            if not _same(current, requested):
                current = await catalog.update(requested, current.revision, actor_id=actor)
        return f"{current.id}@{current.revision}"

    async def _tool(self, requested: ToolDefinition, actor: str) -> None:
        try:
            current = await self.services.capabilities.get("tool", requested.ref, actor_id=actor)
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
            await self.services.capabilities.register_tool(requested, actor_id=actor)
        else:
            if current != requested:
                raise OpsMeshError("CONFLICT", "Tool definition changed without a new version")

    async def _workspace(
        self, definition: WorkspaceDefinition, files: dict[str, bytes], actor: str
    ) -> None:
        await self.services.workspaces.create(definition.id, actor_id=actor)
        for path, content in files.items():
            await self._file(definition.id, path, content, actor)

    async def _file(self, workspace: str, path: str, content: bytes, actor: str) -> None:
        manager = self.services.workspaces
        try:
            existing = await manager.read(workspace, path, actor_id=actor)
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
            digest = None
        else:
            info = await manager.stat(workspace, path, actor_id=actor)
            if existing == content and info.read_only:
                return
            digest = hashlib.sha256(existing).hexdigest()
        await manager.replace_managed_file(
            workspace, path, content, actor_id=actor, expected_digest=digest, read_only=True
        )

    async def _skill(
        self, source: SkillSourceDefinition, files: dict[str, bytes], actor: str
    ) -> SkillDefinition:
        digest = hashlib.sha256(
            json.dumps(
                {
                    "description": source.description,
                    "files": [
                        (path, hashlib.sha256(data).hexdigest())
                        for path, data in sorted(files.items())
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:20]
        # Freeze the inspected sources for publication, omitting interpreter caches.
        with TemporaryDirectory(prefix="opsmesh-skill-") as temporary:
            directory = Path(temporary)
            for path, data in files.items():
                target = directory / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            try:
                return await self.services.skills.publish(
                    source.name,
                    source.version,
                    directory,
                    actor_id=actor,
                    description=source.description,
                )
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
            return await self.services.skills.publish(
                source.name,
                f"{source.version}-{digest}",
                directory,
                actor_id=actor,
                description=source.description,
            )

    async def _skill_files(
        self,
        agent: AgentSpecification,
        definition: ApplicationDefinition,
        skills: dict[str, str],
        skill_sources: dict[str, dict[str, bytes]],
        actor: str,
    ) -> None:
        if not agent.workspace_id:
            return
        workspace = next(
            (item for item in definition.workspaces if item.id == agent.workspace_id), None
        )
        if workspace is None:
            return
        # Refresh managed materializations as part of an offline deployment. Runtime
        # loaders deliberately refuse overwriting another immutable skill version.
        for source in definition.skills:
            if skills[source.ref] not in agent.skill_refs:
                continue
            for path, content in skill_sources[source.ref].items():
                target = f".agents/{source.name}/{path}"
                if any(_within(target, prefix) for prefix in workspace.protected_paths):
                    raise OpsMeshError(
                        "CONFIG_INVALID", "Skill materialization overlaps protected data"
                    )
                await self._file(agent.workspace_id, target, content, actor)

    @staticmethod
    def _agent_order(agents: tuple[AgentSpecification, ...]) -> tuple[AgentSpecification, ...]:
        by_id = {item.id: item for item in agents}
        ordered: list[AgentSpecification] = []
        visiting: set[str] = set()
        complete: set[str] = set()

        def visit(agent: AgentSpecification) -> None:
            if agent.id in complete:
                return
            if agent.id in visiting:
                raise OpsMeshError("CONFIG_INVALID", "Application Agent dependencies are cyclic")
            visiting.add(agent.id)
            dependencies = [item.agent_ref for item in agent.handoffs] + [
                item.agent_ref for item in agent.agent_tools
            ]
            for reference in dependencies:
                identifier = reference.split("@", 1)[0]
                if identifier in by_id:
                    visit(by_id[identifier])
            visiting.remove(agent.id)
            complete.add(agent.id)
            ordered.append(agent)

        for agent in agents:
            visit(agent)
        return tuple(ordered)

    @staticmethod
    def _agent_references(
        specification: AgentSpecification,
        references: dict[str, str],
        models: dict[str, str],
        agents: dict[str, str],
    ) -> AgentSpecification:
        def mapped(kind: str, value: str) -> str:
            return references.get(f"{kind}:{value}", value)

        model = specification.model_profile_ref
        return specification.model_copy(
            update={
                "model_profile_ref": models.get(model, mapped("model", model)) if model else None,
                "skill_refs": tuple(mapped("skill", value) for value in specification.skill_refs),
                "handoffs": tuple(
                    item.model_copy(
                        update={
                            "agent_ref": agents.get(item.agent_ref, mapped("agent", item.agent_ref))
                        }
                    )
                    for item in specification.handoffs
                ),
                "agent_tools": tuple(
                    item.model_copy(
                        update={
                            "agent_ref": agents.get(item.agent_ref, mapped("agent", item.agent_ref))
                        }
                    )
                    for item in specification.agent_tools
                ),
            }
        )

    async def _agent(self, requested: AgentSpecification, actor: str) -> str:
        catalog = self.services.agents
        try:
            current = await catalog.get(requested.id, actor_id=actor)
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
            current = await catalog.create(requested, actor_id=actor)
        if not current.enabled:
            raise OpsMeshError("CONFLICT", "Deployment cannot implicitly enable a disabled Agent")
        if not _same(current.draft, requested):
            current = await catalog.update(current.id, requested, current.revision, actor_id=actor)
        if current.published_revision is not None:
            published = await catalog.get_revision(
                f"{current.id}@{current.published_revision}", actor_id=actor
            )
            if _same(published, requested):
                return published.ref
        return (await catalog.publish(current.id, current.revision, actor_id=actor)).ref

    @staticmethod
    def _grant(
        grant: PermissionGrant,
        references: dict[str, str],
        models: dict[str, str],
        agents: dict[str, str],
    ) -> PermissionGrant:
        resource = grant.resource
        if grant.action == "agent:run":
            resource = references.get(f"agent:{resource}", agents.get(resource, resource))
        elif grant.action == "model:use":
            resource = references.get(f"model:{resource}", models.get(resource, resource))
        elif grant.action == "capability:use" and resource.startswith("skill:"):
            resource = "skill:" + references.get(resource, resource.removeprefix("skill:"))
        return grant.model_copy(update={"resource": resource})

    async def _role(self, requested: AccessRole, actor: str) -> None:
        manager = self.services.accounts
        try:
            current = await manager.get_role(requested.name, actor_id=actor)
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
            await manager.create_role(requested, actor_id=actor)
        else:
            if not _same(current, requested):
                await manager.update_role(
                    requested, actor_id=actor, expected_revision=current.revision
                )

    async def _memory(self, requested: MemoryNamespaceDefinition, actor: str) -> None:
        manager = self.services.memory
        settings = requested.settings
        try:
            current = await manager.get_namespace(requested.id, principal_id=actor)
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
            await manager.create_namespace(
                MemoryNamespace(id=requested.id, owner=actor, **settings.model_dump()),
                actor_id=actor,
            )
            return
        if current.owner != actor:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Only memory owners may publish its policy")
        prior = MemoryNamespaceSettings(
            readers=current.readers, contributors=current.contributors, policy=current.policy
        )
        if settings != prior:
            await manager.configure_namespace(
                requested.id, settings, actor_id=actor, expected_revision=current.revision
            )

    async def _bot(self, requested: BotAccount, actor: str) -> None:
        manager = self.services.bots
        try:
            current = await manager.get_account(requested.id, actor_id=actor)
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
            await manager.create_account(requested, actor_id=actor)
        else:
            if not _same(current, requested):
                await manager.update_account(
                    requested, expected_revision=current.revision, actor_id=actor
                )

    async def _route(self, requested: BotRoute, actor: str) -> None:
        manager = self.services.bots
        try:
            current = await manager.get_route(
                requested.bot_id, requested.channel_id, actor_id=actor
            )
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
        else:
            if current == requested:
                return
        await manager.bind_route(
            requested.bot_id,
            requested.channel_id,
            requested.agent_revision,
            actor_id=actor,
            dispatch_mode=requested.dispatch_mode,
        )
