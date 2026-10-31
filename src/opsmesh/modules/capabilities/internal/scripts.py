"""Immutable Python tools with isolated venvs and hash-verified offline wheel dependencies."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.infrastructure.database.availability import PersistentVersionAvailability
from opsmesh.modules.capabilities.api import (
    CapabilityCatalog,
    ScriptToolDefinition,
    ScriptToolSource,
    ToolAuthorization,
    ToolDefinition,
)
from opsmesh.modules.execution.api import (
    CommandRequest,
    ExecutionProvider,
    ExecutionSession,
    ExecutionSpec,
)
from opsmesh.modules.workspaces.api import WorkspaceArtifact, WorkspaceService

from .serialization import canonical


def _safe_path(value: str) -> str:
    parts = PurePosixPath(value).parts
    if (
        not parts
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or any(part.casefold() in {".", "..", ".venv", ".git"} for part in parts)
    ):
        raise OpsMeshError("PATH_INVALID", "Script asset path must be a safe relative path")
    return value


class PersistentScriptTools:
    def __init__(
        self,
        database: AsyncDatabase,
        workspaces: WorkspaceService,
        catalog: CapabilityCatalog,
        authorize: ToolAuthorization,
        *,
        execution_provider: ExecutionProvider,
        execution_template: ExecutionSpec,
        max_files: int = 1000,
        max_bytes: int = 50_000_000,
    ) -> None:
        self._records = VersionedRecordStore(database, "capability_scripts")
        self.availability = PersistentVersionAvailability(database, "capability_scripts")
        self._workspaces, self._catalog, self._authorize = workspaces, catalog, authorize
        self._provider, self._template = execution_provider, execution_template
        self.max_files, self.max_bytes = max_files, max_bytes

    async def publish(self, source: Path, declaration: ScriptToolSource) -> ScriptToolDefinition:
        try:
            Draft202012Validator.check_schema(declaration.input_schema)
        except SchemaError as error:
            raise OpsMeshError("CONFIG_INVALID", "Script input schema is invalid") from error
        _safe_path(declaration.entrypoint)
        if not declaration.entrypoint.endswith(".py"):
            raise OpsMeshError("CONFIG_INVALID", "Script entrypoint must be a Python source file")
        for wheel in declaration.wheels:
            if not _safe_path(wheel).endswith(".whl"):
                raise OpsMeshError(
                    "CONFIG_INVALID", "Script dependencies must be pinned wheel assets"
                )
        files = await self._artifacts(source)
        paths = {item.path for item in files}
        if not {declaration.entrypoint, *declaration.wheels} <= paths:
            raise OpsMeshError("NOT_FOUND", "Script entrypoint or dependency wheel is missing")
        definition = ScriptToolDefinition(
            **declaration.model_dump(),
            files=files,
            digest=hashlib.sha256(canonical([file.model_dump() for file in files])).hexdigest(),
        )
        prior = await self._records.get(definition.ref)
        if prior is not None and prior.data != definition.model_dump(mode="json"):
            raise OpsMeshError("CONFLICT", "Script tool versions are immutable")
        if prior is None:
            await self._records.create(definition.ref, definition.model_dump(mode="json"))
        await self._catalog.register_tool(
            ToolDefinition(
                name=definition.name,
                version=definition.version,
                description=definition.description,
                input_schema=definition.input_schema,
                code_ref=f"script:{definition.ref}",
                side_effects=definition.side_effects,
                timeout_seconds=definition.timeout_seconds + 180,
            )
        )
        return definition

    async def _artifacts(self, source: Path) -> tuple[WorkspaceArtifact, ...]:
        if source.is_symlink() or not source.is_dir():
            raise OpsMeshError("PATH_INVALID", "Script source must be a trusted directory")
        source = source.resolve()
        files: list[WorkspaceArtifact] = []
        total = 0
        for path in sorted(source.rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise OpsMeshError("PATH_INVALID", "Script bundles cannot contain symlinks")
            if path.is_dir():
                continue
            if not path.is_file():
                raise OpsMeshError("PATH_INVALID", "Script bundles require regular files")
            relative = _safe_path(path.relative_to(source).as_posix())
            total += path.stat().st_size
            if total > self.max_bytes or len(files) >= self.max_files:
                raise OpsMeshError("RESOURCE_LIMIT", "Script bundle exceeds file or byte limit")
            content = await asyncio.to_thread(path.read_bytes)
            files.append(
                WorkspaceArtifact(
                    path=relative,
                    digest=await self._workspaces.put_blob(content),
                    size=len(content),
                    read_only=True,
                )
            )
        return tuple(files)

    async def list(self) -> tuple[ScriptToolDefinition, ...]:
        return tuple(
            ScriptToolDefinition.model_validate(row.data) for row in await self._records.list()
        )

    async def get(self, reference: str, *, include_disabled: bool = False) -> ScriptToolDefinition:
        if not include_disabled:
            await self.availability.require_enabled(reference)
        record = await self._records.get(reference)
        if record is None:
            raise OpsMeshError("NOT_FOUND", "Script tool version does not exist")
        definition = ScriptToolDefinition.model_validate(record.data)
        digest = hashlib.sha256(
            canonical([file.model_dump() for file in definition.files])
        ).hexdigest()
        if digest != definition.digest:
            raise OpsMeshError("CONTENT_CORRUPT", "Script artifact manifest changed")
        return definition

    async def execute(self, reference: str, arguments: dict[str, Any], *, principal_id: str) -> Any:
        await self._authorize(principal_id, reference)
        definition = await self.get(reference)
        try:
            Draft202012Validator(definition.input_schema).validate(arguments)
        except ValidationError as error:
            raise OpsMeshError(
                "INPUT_INVALID", "Script arguments do not match the schema"
            ) from error
        with TemporaryDirectory(prefix="opsmesh-script-") as temporary:
            root = Path(temporary)
            for artifact in definition.files:
                target = root / _safe_path(artifact.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(
                    target.write_bytes, await self._workspaces.get_blob(artifact.digest)
                )
            input_file = f".opsmesh-input-{uuid4().hex}.json"
            await asyncio.to_thread((root / input_file).write_bytes, canonical(arguments))
            spec = self._template.model_copy(update={"workspace": root})
            session = await self._provider.create(spec)
            try:
                interpreter = await self._environment(session, definition, root)
                output = await self._command(
                    session,
                    (interpreter, definition.entrypoint, input_file),
                    timeout=definition.timeout_seconds,
                )
                try:
                    result = json.loads(output)
                    canonical(
                        result
                    )  # Reject non-finite numbers before crossing a service boundary.
                    return result
                except ValueError as error:
                    raise OpsMeshError(
                        "SCRIPT_OUTPUT_INVALID", "Script stdout must contain one JSON value"
                    ) from error
            finally:
                await session.close()

    async def _environment(
        self, session: ExecutionSession, definition: ScriptToolDefinition, root: Path
    ) -> str:
        local = self._template.provider == "local"
        python = sys.executable if local else "python"
        await self._command(session, (python, "-m", "venv", ".venv"), timeout=60)
        interpreter = (
            ".venv/Scripts/python.exe" if local and os.name == "nt" else ".venv/bin/python"
        )
        if local:
            interpreter = str(root / interpreter)
        if definition.wheels:
            await self._command(
                session,
                (
                    interpreter,
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--no-index",
                    "--no-deps",
                    "--disable-pip-version-check",
                    *definition.wheels,
                ),
                timeout=60,
            )
            await self._command(
                session, (interpreter, "-m", "pip", "--isolated", "check"), timeout=30
            )
        return interpreter

    @staticmethod
    async def _command(session: ExecutionSession, argv: tuple[str, ...], *, timeout: float) -> str:
        result = await session.execute(
            CommandRequest(id=uuid4().hex, argv=argv, timeout_seconds=timeout)
        )
        if result.output_truncated:
            raise OpsMeshError("RESOURCE_LIMIT", "Script output exceeded the output limit")
        if result.exit_code:
            raise OpsMeshError(
                "SCRIPT_EXECUTION_FAILED",
                "Script or environment setup failed",
                details={"exit_code": result.exit_code},
            )
        return result.stdout
