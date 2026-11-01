"""Compose native sandbox lifecycles with durable platform workspace APIs."""

from __future__ import annotations

import hashlib
import io
import tarfile
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from opsmesh.framework.agents import (
    AgentExecutionContext,
    AgentRunCompleted,
    AgentSpecification,
    SandboxConfiguration,
)
from opsmesh.framework.agents.resources import (
    AgentExecutionResources,
    AgentExecutionState,
    SandboxProviderBinding,
    SandboxProviderFactory,
    SandboxSession,
    SandboxSessionFactory,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, StoredRecord, VersionedRecordStore
from opsmesh.modules.runtime.api import AgentRunView
from opsmesh.modules.workspaces.api import WorkspaceService


def archive_files(
    raw: bytes, *, max_members: int, max_bytes: int
) -> tuple[list[tuple[str, bytes, bool]], tuple[str, ...]]:
    """Inspect tar members without extracting paths or following archive links."""
    files: list[tuple[str, bytes, bool]] = []
    directories: list[str] = []
    seen: set[str] = set()
    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        for index, member in enumerate(archive):
            path = PurePosixPath(member.name)
            if str(path) == ".":
                continue
            if (
                index >= max_members
                or path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or ":" in member.name
            ):
                raise OpsMeshError("ARTIFACT_INVALID", "Unsafe or oversized sandbox archive")
            name = str(path)
            if name in seen or not (member.isfile() or member.isdir()):
                raise OpsMeshError(
                    "ARTIFACT_INVALID",
                    "Sandbox archive contains links, duplicate or special entries",
                )
            seen.add(name)
            if member.isdir():
                directories.append(name)
                continue
            total += member.size
            if total > max_bytes:
                raise OpsMeshError(
                    "RESOURCE_LIMIT_EXCEEDED", "Sandbox output exceeds configured size"
                )
            stream = archive.extractfile(member)
            if stream is None:
                raise OpsMeshError("ARTIFACT_INVALID", "Sandbox archive file is missing")
            data = stream.read(member.size + 1)
            if len(data) != member.size:
                raise OpsMeshError("ARTIFACT_INVALID", "Sandbox archive file is truncated")
            files.append((name, data, not bool(member.mode & 0o222)))
    return files, tuple(directories)


class SandboxWorkspaceBridge:
    def __init__(
        self,
        workspaces: WorkspaceService,
        database: AsyncDatabase,
        *,
        allow_trusted_local: bool = False,
        session_factories: dict[str, SandboxSessionFactory] | None = None,
    ) -> None:
        self.workspaces = workspaces
        self.bindings = VersionedRecordStore(database, "runtime_workspace_bindings")
        self.allow_trusted_local = allow_trusted_local
        self._session_factories = dict(session_factories or {})
        self._active: dict[str, SandboxSession] = {}
        self._providers: dict[str, SandboxProviderFactory] = {}
        self._contexts: dict[str, AgentExecutionContext] = {}

    def _sessions(self, backend: str) -> SandboxSessionFactory:
        factory = self._session_factories.get(backend)
        if factory is None:
            raise OpsMeshError("DEPENDENCY_UNAVAILABLE", "Managed sandbox backend is unavailable")
        return factory

    def register_provider(self, reference: str, factory: SandboxProviderFactory) -> None:
        if "@" not in reference or reference in self._providers:
            raise OpsMeshError(
                "CONFIG_INVALID", "Sandbox providers require unique versioned references"
            )
        self._providers[reference] = factory

    async def _provider(
        self, view: AgentRunView, context: AgentExecutionContext | None
    ) -> SandboxProviderBinding | None:
        assert view.agent.sandbox is not None
        name = view.agent.sandbox.provider
        if name in {"local", "docker"}:
            return None
        factory = self._providers.get(name)
        if factory is None or context is None:
            raise OpsMeshError(
                "DEPENDENCY_UNAVAILABLE", "Sandbox provider or execution context unavailable"
            )
        return await factory(view.agent.sandbox, context)

    async def _source_snapshot(self, view: AgentRunView) -> Any:
        binding = await self.bindings.get(view.id)
        if binding and binding.data.get("snapshot_id"):
            return await self.workspaces.get_snapshot(binding.data["snapshot_id"])
        workspace_id = view.agent.workspace_id or view.metadata.get("workspace_id")
        configuration = view.agent.sandbox
        selected = None
        if configuration and configuration.snapshot_ref:
            selected = await self.workspaces.get_snapshot(configuration.snapshot_ref)
            await self._authorize_workspace(view, selected.workspace_id)
            workspace_id = selected.workspace_id
        elif configuration and configuration.session_ref:
            source = await self._resume_source(view)
            selected = await self.workspaces.get_snapshot(source.data["snapshot_id"])
            await self._authorize_workspace(view, selected.workspace_id)
            workspace_id = selected.workspace_id
        if workspace_id is None:
            workspace_id = f"sandbox-source-{view.id}"
            try:
                await self.workspaces.create(workspace_id)
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
        snapshot = selected or await self.workspaces.snapshot(workspace_id)
        if binding is None:
            await self.bindings.create(
                view.id,
                {
                    "source_workspace_id": workspace_id,
                    "snapshot_id": snapshot.id,
                    "finalized": False,
                    "principal_id": view.principal_id,
                    "provider": configuration.provider if configuration else None,
                    "provider_options": configuration.options if configuration else {},
                },
            )
        return snapshot

    async def _authorize_workspace(self, view: AgentRunView, workspace_id: str) -> None:
        context = self._contexts.get(view.id)
        authorize = context.services.authorize_workspace if context else None
        if authorize is not None:
            await authorize(workspace_id)
        elif workspace_id != view.agent.workspace_id:
            raise OpsMeshError(
                "AUTHORIZATION_DENIED", "Snapshot workspace authorization unavailable"
            )

    async def _session_source(
        self, principal_id: str, configuration: SandboxConfiguration
    ) -> StoredRecord:
        source = await self.bindings.get(configuration.session_ref or "")
        if source is None or source.data.get("principal_id") != principal_id:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Sandbox state belongs to another principal")
        if (
            source.data.get("provider") != configuration.provider
            or source.data.get("provider_options") != configuration.options
        ):
            raise OpsMeshError("CHECKPOINT_INCOMPATIBLE", "Sandbox provider configuration changed")
        if not source.data.get("resume_state") or not source.data.get("snapshot_id"):
            raise OpsMeshError(
                "CHECKPOINT_INCOMPATIBLE", "Sandbox has no committed resumable state"
            )
        return source

    async def authorize_session_source(
        self, principal_id: str, configuration: SandboxConfiguration
    ) -> str:
        """Validate persisted identity and return its workspace for the caller's ACL check."""
        source = await self._session_source(principal_id, configuration)
        snapshot = await self.workspaces.get_snapshot(source.data["snapshot_id"])
        return snapshot.workspace_id

    async def _resume_source(self, view: AgentRunView) -> StoredRecord:
        assert view.agent.sandbox is not None
        return await self._session_source(view.principal_id, view.agent.sandbox)

    async def prepare(
        self, view: AgentRunView, context: AgentExecutionContext | None = None
    ) -> AgentExecutionResources:
        async def delegated(
            specification: AgentSpecification, parent: AgentExecutionContext, call_id: str
        ) -> AgentExecutionContext:
            return await self.prepare_delegated(view, specification, parent, call_id)

        configuration = view.agent.sandbox
        if configuration is None or view.has_final_outcome:
            return AgentExecutionResources(prepare_delegated_sandbox=delegated)
        if configuration.provider == "local" and not self.allow_trusted_local:
            raise OpsMeshError("FORBIDDEN", "Trusted local sandboxes are disabled")
        sessions = self._sessions(view.agent.backend)
        if context is not None:
            self._contexts[view.id] = context
        await self._close(view)
        provider = await self._provider(view, context)
        snapshot = await self._source_snapshot(view)
        if configuration.session_ref:
            source = await self._resume_source(view)
            configuration = configuration.model_copy(
                update={"session_state": source.data["resume_state"]}
            )
        files = tuple(
            [
                (entry.path, await self.workspaces.get_blob(entry.digest), entry.read_only)
                for entry in snapshot.entries
            ]
        )
        if provider is None and not configuration.session_ref:
            manager = await sessions.open(configuration, files, snapshot.directories)
        else:
            manager = await sessions.open(
                configuration,
                files,
                snapshot.directories,
                context=context,
                provider=provider,
                trusted_source=bool(configuration.session_ref),
            )
        self._active[view.id] = manager
        record = await self.bindings.get(view.id)
        assert record is not None
        try:
            await self.bindings.update(
                view.id,
                dict(
                    record.data, cleanup_state=manager.cleanup_state(), attempt_id=view.attempt_id
                ),
                record.revision,
            )
        except BaseException:
            await manager.close()
            if self._active.get(view.id) is manager:
                self._active.pop(view.id, None)
            raise

        async def checkpoint() -> dict[str, Any]:
            metadata = await self._capture(view, finalized=False)
            await self._close(view, owned_manager=manager)
            return metadata

        async def cleanup() -> None:
            try:
                await self._close(view, owned_manager=manager)
            finally:
                if self._contexts.get(view.id) is context:
                    self._contexts.pop(view.id, None)

        return AgentExecutionResources(
            sandbox_session=manager.session,
            sandbox_lifecycle=manager,
            sandbox_root_specification=view.agent,
            sync_resources=manager.materialize_files,
            checkpoint_resources=checkpoint,
            cleanup_resources=cleanup,
            prepare_delegated_sandbox=delegated,
        )

    async def prepare_delegated(
        self,
        parent_view: AgentRunView,
        specification: AgentSpecification,
        parent: AgentExecutionContext,
        call_id: str,
    ) -> AgentExecutionContext:
        if not call_id:
            raise OpsMeshError("CONFIG_INVALID", "Delegated sandbox requires a stable tool call id")
        identifier = (
            "child-"
            + hashlib.sha256(f"{parent_view.id}:{specification.ref}:{call_id}".encode()).hexdigest()
        )
        child_view = parent_view.model_copy(
            update={
                "id": identifier,
                "agent": specification,
                "has_final_outcome": False,
                "has_checkpoint": False,
            }
        )
        child = replace(
            parent,
            services=replace(parent.services),
            resources=AgentExecutionResources(),
            state=AgentExecutionState(attempt_id=parent.state.attempt_id),
            backend_state=None,
            extensions=dict(parent.extensions),
        )
        if specification.workspace_id:
            authorize = child.services.authorize_workspace
            if authorize is None:
                raise OpsMeshError(
                    "AUTHORIZATION_DENIED", "Delegated workspace authorization unavailable"
                )
            await authorize(specification.workspace_id)
        bindings = await self.prepare(child_view, child)
        child.resources = bindings
        checkpoint = bindings.checkpoint_resources
        if checkpoint is None:
            raise OpsMeshError("CONTEXT_INVALID", "Delegated sandbox has no checkpoint owner")

        async def save() -> dict[str, Any]:
            metadata: dict[str, Any] = await checkpoint()
            metadata["delegated_agent"] = specification.ref
            metadata["tool_call_id"] = call_id
            claim = parent.resources.claim_workspace
            if claim is not None:
                await claim(metadata)
            parent.state.delegated_artifacts.append(metadata)
            return metadata

        async def invoke(tool_ref: str, arguments: dict[str, Any], nested_call_id: str) -> Any:
            value = await parent.invoke_tool(tool_ref, arguments, f"{call_id}:{nested_call_id}")
            synchronize = child.services.sync_agent_resources
            if synchronize is not None:
                await synchronize(child, tool_ref, arguments)
            return value

        child.invoke_tool = invoke
        child.resources.checkpoint_resources = save
        return child

    async def _capture(self, view: AgentRunView, *, finalized: bool) -> dict[str, Any]:
        record = await self.bindings.get(view.id)
        if record is not None and finalized and record.data.get("finalized"):
            metadata: dict[str, Any] = record.data["metadata"]
            return metadata
        if record is not None and record.data.get("attempt_id") != view.attempt_id:
            raise OpsMeshError("OWNERSHIP_LOST", "Sandbox belongs to a different execution attempt")
        manager = self._active.get(view.id)
        if manager is None:
            raise OpsMeshError(
                "ARTIFACT_UNAVAILABLE", "Sandbox output was not durably captured before worker loss"
            )
        limits = view.agent.sandbox.archive_limits if view.agent.sandbox else {}
        files, directories = archive_files(
            await manager.archive(),
            max_members=limits.get("max_members", 10000),
            max_bytes=limits.get("max_extracted_bytes", 500_000_000),
        )
        output_id = f"sandbox-output-{uuid4().hex}"
        await self.workspaces.create(output_id)
        from opsmesh.modules.workspaces.api import WorkspaceManifest

        await self.workspaces.materialize(output_id, WorkspaceManifest(directories=directories))
        for path, data, read_only in files:
            await self.workspaces.write(output_id, path, data, read_only=read_only)
        snapshot = await self.workspaces.snapshot(output_id)
        metadata = {
            "workspace_id": output_id,
            "workspace_snapshot_id": snapshot.id,
            "artifacts": [entry.model_dump(mode="json") for entry in snapshot.entries],
        }
        if memories := getattr(manager, "memories", None):
            metadata["memory_generations"] = [memory.receipt() for memory in memories]
        assert record is not None
        await self.bindings.update(
            view.id,
            dict(
                record.data,
                snapshot_id=snapshot.id,
                finalized=finalized,
                metadata=metadata,
                resume_state=manager.cleanup_state(),
            ),
            record.revision,
        )
        return metadata

    async def finalize(self, view: AgentRunView, outcome: AgentRunCompleted) -> dict[str, Any]:
        if view.agent.sandbox is None:
            return {}
        metadata = await self._capture(view, finalized=True)
        await self._close(view)
        return metadata

    async def _close(
        self, view: AgentRunView, *, owned_manager: SandboxSession | None = None
    ) -> None:
        record = await self.bindings.get(view.id)
        manager = owned_manager or self._active.get(view.id)
        if manager is None and record and record.data.get("cleanup_state"):
            assert view.agent.sandbox is not None
            manager = await self._sessions(view.agent.backend).resume_cleanup(
                view.agent.sandbox,
                record.data["cleanup_state"],
                provider=await self._provider(view, self._contexts.get(view.id)),
            )
        if manager is not None:
            await manager.close()
            if self._active.get(view.id) is manager:
                self._active.pop(view.id, None)
            assert record is not None
            if owned_manager is not None and record.data.get("attempt_id") != view.attempt_id:
                return  # A previous worker must not clear its replacement's cleanup receipt.
            await self.bindings.update(
                view.id, dict(record.data, cleanup_state=None), record.revision
            )

    async def close(self) -> None:
        for manager in tuple(self._active.values()):
            await manager.close()
        self._active.clear()
        self._contexts.clear()
