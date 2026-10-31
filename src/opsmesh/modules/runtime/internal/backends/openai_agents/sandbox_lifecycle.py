"""Own native SDK sessions while the platform owns durable workspace snapshots."""

import asyncio
from pathlib import PurePosixPath
from typing import Any

from agents.sandbox import Manifest
from agents.sandbox.entries import Dir, File, resolve_workspace_path
from agents.sandbox.errors import WorkspaceReadNotFoundError
from agents.sandbox.types import Permissions

from opsmesh.framework.agents import AgentExecutionContext, SandboxConfiguration
from opsmesh.framework.errors import OpsMeshError

from .sandbox import build_sandbox


def validate_managed_manifest(
    configuration: SandboxConfiguration, *, trusted_source: bool = False
) -> None:
    manifest = configuration.manifest
    if manifest.get("root", "/workspace") != "/workspace" or manifest.get("extra_path_grants"):
        raise OpsMeshError("FORBIDDEN", "Managed workspaces cannot grant host paths")
    if (configuration.snapshot_path or configuration.session_state) and not trusted_source:
        raise OpsMeshError("FORBIDDEN", "Managed workspace snapshots are selected by the runtime")

    def check(entries: dict[str, Any]) -> None:
        for entry in entries.values():
            if entry.get("type") not in {"file", "dir", "git_repo"}:
                raise OpsMeshError(
                    "FORBIDDEN",
                    "Managed manifests only accept inline files, directories and Git repositories",
                )
            if entry.get("type") == "dir":
                check(entry.get("children", {}))

    check(manifest.get("entries", {}))


class ManagedSandboxSession:
    def __init__(
        self, client: Any, session: Any, max_bytes: int, *, provider_close: Any = None
    ) -> None:
        self.client = client
        self.session = session
        self.max_bytes = max_bytes
        self.closed = False
        self.memories: list[Any] = []
        self._provider_close = provider_close

    @classmethod
    async def open(
        cls,
        configuration: SandboxConfiguration,
        files: tuple[tuple[str, bytes, bool], ...],
        directories: tuple[str, ...],
        *,
        context: AgentExecutionContext | None = None,
        provider: Any = None,
        trusted_source: bool = False,
    ) -> "ManagedSandboxSession":
        try:
            return await cls._open(
                configuration,
                files,
                directories,
                context=context,
                provider=provider,
                trusted_source=trusted_source,
            )
        except BaseException:
            if provider is not None and provider.aclose is not None:
                await provider.aclose()
            raise

    @classmethod
    async def _open(
        cls,
        configuration: SandboxConfiguration,
        files: tuple[tuple[str, bytes, bool], ...],
        directories: tuple[str, ...],
        *,
        context: AgentExecutionContext | None = None,
        provider: Any = None,
        trusted_source: bool = False,
    ) -> "ManagedSandboxSession":
        if provider is None or provider.validate_manifest is None:
            validate_managed_manifest(configuration, trusted_source=trusted_source)
        else:
            if context is None:
                raise OpsMeshError(
                    "AUTHORIZATION_DENIED", "Provider manifest requires execution context"
                )
            await provider.validate_manifest(configuration.manifest, context)

        async def unused(*args: Any) -> Any:
            raise AssertionError("Sandbox construction does not invoke tools")

        construction_context = AgentExecutionContext("", "", "", unused)
        if provider is not None:
            construction_context.services.update(
                {
                    "openai_sandbox_client": provider.client,
                    "openai_sandbox_options": provider.options,
                }
            )
        config = build_sandbox(configuration, construction_context)
        client = config.client
        if client is None:
            raise OpsMeshError(
                "FEATURE_UNSUPPORTED", "Managed workspace requires a native sandbox provider"
            )
        manifest = Manifest.model_validate(configuration.manifest)
        entries = dict(manifest.entries)
        for path in directories:
            entries.setdefault(path, Dir())
        for path, content, read_only in files:
            entries[path] = File(
                content=content, permissions=Permissions(owner=4 if read_only else 6)
            )
        manifest = manifest.model_copy(update={"entries": entries})
        if config.session_state is not None:
            manifest = manifest.model_copy(update={"root": config.session_state.manifest.root})
            state = config.session_state.model_copy(update={"manifest": manifest})
            session = await client.resume(state)
        else:
            session = await client.create(
                manifest=manifest,
                options=config.options,
                **({"snapshot": config.snapshot} if config.snapshot is not None else {}),
            )
        manager = cls(
            client,
            session,
            configuration.archive_limits.get("max_input_bytes", 100_000_000),
            provider_close=provider.aclose if provider is not None else None,
        )
        session._set_concurrency_limits(config.concurrency_limits)
        session._set_archive_limits(config.archive_limits)
        try:
            await session.start()
            if config.session_state is not None:
                for relative in directories:
                    await session.mkdir(relative, parents=True)
                for relative, content, read_only in files:
                    entry = File(
                        content=content, permissions=Permissions(owner=4 if read_only else 6)
                    )
                    await entry.apply(
                        session,
                        resolve_workspace_path(manifest.root, relative),
                        resolve_workspace_path(manifest.root, "."),
                    )
        except BaseException:
            await manager.close()
            raise
        return manager

    def cleanup_state(self) -> dict[str, Any]:
        state = self.session.state.model_copy(
            update={"manifest": Manifest(root=self.session.state.manifest.root)}
        )
        return dict(self.client.serialize_session_state(state))

    @classmethod
    async def resume_cleanup(
        cls,
        configuration: SandboxConfiguration,
        state: dict[str, Any],
        *,
        provider: Any = None,
    ) -> "ManagedSandboxSession":
        try:
            return await cls._resume_cleanup(configuration, state, provider=provider)
        except BaseException:
            if provider is not None and provider.aclose is not None:
                await provider.aclose()
            raise

    @classmethod
    async def _resume_cleanup(
        cls,
        configuration: SandboxConfiguration,
        state: dict[str, Any],
        *,
        provider: Any = None,
    ) -> "ManagedSandboxSession":
        async def unused(*args: Any) -> Any:
            raise AssertionError("Cleanup does not invoke tools")

        construction_context = AgentExecutionContext("", "", "", unused)
        if provider is not None:
            construction_context.services.update(
                {
                    "openai_sandbox_client": provider.client,
                    "openai_sandbox_options": provider.options,
                }
            )
        config = build_sandbox(configuration, construction_context)
        client = config.client
        if client is None:
            raise OpsMeshError("FEATURE_UNSUPPORTED", "Sandbox cleanup provider is unavailable")
        session = await client.resume(client.deserialize_session_state(state))
        return cls(
            client,
            session,
            100_000_000,
            provider_close=provider.aclose if provider is not None else None,
        )

    async def archive(self) -> bytes:
        await self.flush_memory()
        stream = await self.session.persist_workspace()
        try:
            raw = await asyncio.to_thread(stream.read, self.max_bytes + 1)
            if not isinstance(raw, bytes) or len(raw) > self.max_bytes:
                raise OpsMeshError(
                    "RESOURCE_LIMIT_EXCEEDED", "Sandbox output archive exceeds configured size"
                )
            return raw
        finally:
            stream.close()

    async def flush_memory(self) -> list[dict[str, Any]]:
        for memory in self.memories:
            await memory.flush()
        return [memory.receipt() for memory in self.memories]

    async def materialize_files(self, files: tuple[tuple[str, bytes, bool], ...]) -> None:
        for relative, content, read_only in files:
            path = PurePosixPath(relative)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in relative
                or ":" in relative
                or len(path.parts) < 3
                or path.parts[0] != ".agents"
            ):
                raise OpsMeshError(
                    "FORBIDDEN", "Dynamic resources must stay inside the skill directory"
                )
            destination = resolve_workspace_path(self.session.state.manifest.root, relative)
            try:
                existing = await self.session.read_bounded(
                    destination, max_bytes=max(len(content), 1) + 1
                )
            except WorkspaceReadNotFoundError:
                existing = None
            if existing is not None:
                if existing != content:
                    raise OpsMeshError(
                        "CONFLICT", "Skill resource conflicts with existing sandbox content"
                    )
                if read_only:
                    await self.session._exec_checked_nonzero(
                        "chmod", "0400", destination.as_posix()
                    )
                continue
            entry = File(content=content, permissions=Permissions(owner=4 if read_only else 6))
            await entry.apply(
                self.session,
                destination,
                resolve_workspace_path(self.session.state.manifest.root, "."),
            )

    async def close(self) -> None:
        if self.closed:
            return
        try:
            await self.session.aclose()
        finally:
            try:
                await self.client.delete(self.session)
            finally:
                try:
                    for memory in self.memories:
                        await memory.close_clients()
                finally:
                    try:
                        if self._provider_close is not None:
                            await self._provider_close()
                    finally:
                        self.closed = True
