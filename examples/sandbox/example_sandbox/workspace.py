"""Persist SDK sandbox state and close live resources after every turn."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import anyio
from agents.sandbox import Manifest
from agents.sandbox.session.sandbox_client import BaseSandboxClient
from agents.sandbox.session.sandbox_session import SandboxSession

from . import filesystem


class SandboxWorkspaces:
    """Keep one workspace per conversation in the filesystem plugin space.

    UnixLocalSandbox runs trusted commands on the Linux host. Separate
    directories do not provide operating-system isolation.
    """

    def __init__(self, client: BaseSandboxClient) -> None:
        """Borrow a native client; retain no live sessions across requests."""
        self.client = client

    @staticmethod
    def directory(session_id: str) -> Path:
        """Return a server-derived path; never accept a caller-supplied path."""
        return filesystem.data_file("workspaces/" + str(UUID(session_id)))

    @staticmethod
    def state_file(session_id: str) -> Path:
        """Keep reconnect state outside the model's working directory."""
        return filesystem.data_file("states/" + str(UUID(session_id)) + ".json")

    @asynccontextmanager
    async def open(self, session_id: str) -> AsyncIterator[SandboxSession]:
        """Resume and save native state, then close processes and I/O."""
        directory = self.directory(session_id)
        state_file = self.state_file(session_id)
        directory.parent.mkdir(parents=True, exist_ok=True)
        if (
            directory.is_symlink()
            or directory.resolve() != directory.absolute()
        ):
            raise ValueError("Sandbox workspace must not traverse symlinks")
        if state_file.exists():
            payload = json.loads(await filesystem.read_file(state_file))
            state = self.client.deserialize_session_state(payload)
            if Path(state.manifest.root) != directory:
                raise ValueError(
                    "Persisted sandbox state has an unexpected root"
                )
            if not directory.is_dir():
                raise RuntimeError(
                    "Sandbox files are missing; reset the workspace explicitly"
                )
            session = await self.client.resume(state)
        else:
            session = await self.client.create(
                manifest=Manifest(root=str(directory))
            )
        started = False
        try:
            await session.start()
            started = True
            await self._save(state_file, session)
            yield session
        finally:
            with anyio.fail_after(30, shield=True):
                try:
                    if started:
                        await self._save(state_file, session)
                finally:
                    await session.aclose()

    async def _save(self, path: Path, session: SandboxSession) -> None:
        payload = self.client.serialize_session_state(session.state)
        await filesystem.write_file(path, json.dumps(payload))

    async def reset(self, session_id: str) -> None:
        """Delete only the owned workspace and state, allowing retry."""
        for path in (self.directory(session_id), self.state_file(session_id)):
            if path.exists() or path.is_symlink():
                await filesystem.delete(path)
