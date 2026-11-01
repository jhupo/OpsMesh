"""Immutable capability versions and an authorized, replay-safe tool gateway."""

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Literal

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.infrastructure.database.availability import PersistentVersionAvailability
from opsmesh.modules.capabilities.api import (
    GitSkillSource,
    SkillDefinition,
)
from opsmesh.modules.capabilities.events import CapabilityPublished
from opsmesh.modules.workspaces.api import (
    WorkspaceArtifact,
    WorkspaceEntry,
    WorkspaceManifest,
    WorkspaceService,
)

from .git_skills import GitSkillImporter
from .serialization import canonical


class PersistentSkillCatalog:
    def __init__(
        self,
        database: AsyncDatabase,
        workspaces: WorkspaceService,
        *,
        git_importer: GitSkillImporter | None = None,
    ) -> None:
        self._records = VersionedRecordStore(database, "capability_skills")
        self.availability = PersistentVersionAvailability(database, "capability_skills")
        self._workspaces = workspaces
        self._git_importer = git_importer or GitSkillImporter()

    async def publish_git(
        self, name: str, version: str, source: GitSkillSource, *, description: str = ""
    ) -> SkillDefinition:
        async with self._git_importer.import_source(source) as directory:
            return await self._publish(
                name, version, directory, description=description, provenance=source
            )

    async def publish(
        self, name: str, version: str, source: Path, *, description: str = ""
    ) -> SkillDefinition:
        return await self._publish(name, version, source, description=description)

    async def _publish(
        self,
        name: str,
        version: str,
        source: Path,
        *,
        description: str = "",
        provenance: GitSkillSource | None = None,
    ) -> SkillDefinition:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", name) or not version:
            raise OpsMeshError("CONFIG_INVALID", "Invalid skill identity")
        if source.is_symlink() or not source.is_dir():
            raise OpsMeshError("PATH_INVALID", "Skill source must be a trusted directory")
        source = source.resolve()
        if not (source / "SKILL.md").is_file():
            raise OpsMeshError("CONFIG_INVALID", "Skill requires SKILL.md")
        files: list[WorkspaceArtifact] = []
        for path in sorted(source.rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise OpsMeshError("PATH_INVALID", "Skill cannot contain symlinks")
            if path.is_dir():
                continue
            if not path.is_file():
                raise OpsMeshError("PATH_INVALID", "Skill contains a non-regular file")
            relative = path.relative_to(source).as_posix()
            if "\\" in relative or ":" in relative:
                raise OpsMeshError("PATH_INVALID", "Invalid skill asset path")
            data = await asyncio.to_thread(path.read_bytes)
            files.append(
                WorkspaceArtifact(
                    path=relative,
                    digest=await self._workspaces.put_blob(data),
                    size=len(data),
                    read_only=True,
                )
            )
        if not description:
            markdown = (source / "SKILL.md").read_text(encoding="utf-8")
            match = re.search(r"(?m)^description:\s*(.+)$", markdown)
            description = match.group(1).strip("\"'") if match else name
        digest = hashlib.sha256(
            canonical([item.model_dump(mode="json") for item in files])
        ).hexdigest()
        definition = SkillDefinition(
            name=name,
            version=version,
            description=description,
            files=tuple(files),
            digest=digest,
            source_repository=provenance.repository if provenance else None,
            source_commit=provenance.commit.lower() if provenance else None,
        )
        prior = await self._records.get(definition.ref)
        if prior is not None:
            if prior.data != definition.model_dump(mode="json"):
                raise OpsMeshError("CONFLICT", "Skill versions are immutable")
            return SkillDefinition.model_validate(prior.data)
        await self._records.create(
            definition.ref,
            definition.model_dump(mode="json"),
            events=(
                CapabilityPublished(payload={"ref": definition.ref, "kind": "skill"}).model_dump(
                    mode="json"
                ),
            ),
        )
        return definition

    async def list(self) -> tuple[SkillDefinition, ...]:
        return tuple(SkillDefinition.model_validate(row.data) for row in await self._records.list())

    async def get(self, ref: str, *, include_disabled: bool = False) -> SkillDefinition:
        if not include_disabled:
            await self.availability.require_enabled(ref)
        record = await self._records.get(ref)
        if record is None:
            raise OpsMeshError("NOT_FOUND", "Skill version not found")
        definition = SkillDefinition.model_validate(record.data)
        digest = hashlib.sha256(
            canonical([item.model_dump(mode="json") for item in definition.files])
        ).hexdigest()
        if digest != definition.digest:
            raise OpsMeshError("CONTENT_CORRUPT", "Skill manifest digest mismatch")
        return definition

    async def load(
        self, ref: str, workspace_id: str, *, target: str = ".agents"
    ) -> tuple[WorkspaceArtifact, ...]:
        skill = await self.get(ref)
        pending: list[WorkspaceEntry] = []
        loaded: list[WorkspaceArtifact] = []
        for item in skill.files:
            path = f"{target}/{skill.name}/{item.path}"
            await self._workspaces.get_blob(item.digest)
            try:
                existing = await self._workspaces.read(workspace_id, path)
            except OpsMeshError as error:
                if error.code != "NOT_FOUND":
                    raise
                pending.append(WorkspaceEntry(path=path, digest=item.digest, read_only=True))
            else:
                if hashlib.sha256(existing).hexdigest() != item.digest:
                    raise OpsMeshError("CONFLICT", "Skill target contains different content")
            loaded.append(item.model_copy(update={"path": path}))
        if pending:
            await self._workspaces.materialize(
                workspace_id, WorkspaceManifest(entries=tuple(pending))
            )
        return tuple(loaded)

    async def prepare(
        self, refs: tuple[str, ...], workspace_id: str, *, mode: Literal["eager", "lazy"]
    ) -> tuple[SkillDefinition, ...]:
        if mode not in {"eager", "lazy"}:
            raise OpsMeshError("CONFIG_INVALID", "Unsupported skill loading mode")
        definitions = tuple([await self.get(ref) for ref in refs])
        if len({skill.name for skill in definitions}) != len(definitions):
            raise OpsMeshError("CONFLICT", "Multiple versions share a skill target directory")
        await self._workspaces.get(workspace_id)
        if mode == "eager":
            for ref in refs:
                await self.load(ref, workspace_id)
        return definitions
