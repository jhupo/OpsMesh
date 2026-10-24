import asyncio
import importlib.metadata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path, PurePosixPath

from agents.sandbox.session.sandbox_session import SandboxSession
from opsmesh_sdk import UserIdentity
from opsmesh_sdk.sessions import SandboxContext
from sqlalchemy.orm import sessionmaker

from opsmesh.infra.database import new_id, now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.plugins.loader import load
from opsmesh.plugins.registry import PluginRegistry
from opsmesh.quotas.storage import check
from opsmesh.shared.errors import ConflictError, NotFoundError
from opsmesh.users.service import require_owner

from .layout import session_directory
from .models import AgentSession


def sandbox_path(root: str, name: str, *, allow_root: bool = False) -> Path:
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or ":" in name
        or any(ord(char) < 32 for char in name)
        or (path.parts and path.parts[0] == ".opsmesh")
        or (not path.parts and not allow_root)
    ):
        raise ValueError("Invalid sandbox path or reserved .opsmesh directory")
    return Path(str(PurePosixPath(root) / path))


class SandboxStorage:
    """Own native session lifecycle; resource routes own file operations."""

    def __init__(
        self, sessions: sessionmaker, cipher: SecretCipher, registry: PluginRegistry, root: Path
    ):
        self.sessions, self.cipher, self.registry, self.root = sessions, cipher, registry, root

    async def _renew(self, session_id: str, access_id: str) -> None:
        while True:
            await asyncio.sleep(30)
            with self.sessions.begin() as db:
                row = db.get(AgentSession, session_id, with_for_update=True)
                if row is None or row.sandbox_task_id != access_id or row.closed:
                    raise ConflictError("Sandbox file operation lost its ownership")
                row.sandbox_access_until = now() + timedelta(minutes=2)

    @asynccontextmanager
    async def open(
        self, user: UserIdentity, session_id: str, *, mutate: bool = False
    ) -> AsyncIterator[SandboxSession]:
        access_id = new_id()
        with self.sessions.begin() as db:
            row = db.get(AgentSession, session_id, with_for_update=True)
            if row is None:
                raise NotFoundError("Session not found")
            require_owner(user, row.owner_id)
            if row.closed or row.active_task_id or row.sandbox_dirty:
                raise ConflictError("Sandbox management requires an idle, clean, open session")
            if not row.encrypted_sandbox_state or not row.sandbox_plugin_id:
                raise ConflictError("Run this sandbox agent once before accessing its filesystem")
            if row.sandbox_sdk_version != importlib.metadata.version("openai-agents"):
                raise ConflictError("Sandbox SDK version differs from the runtime")
            plugin = load(db, self.root, self.registry, row.sandbox_plugin_id)
            backend = row.sandbox_backend
            factory = plugin.sandboxes.get(backend or "")
            if factory is None:
                raise ConflictError("The pinned plugin has no sandbox client factory")
            context = SandboxContext(
                user,
                self.cipher.decrypt(row.encrypted_sandbox_config or ""),
                session_directory(self.root, row),
            )
            state = self.cipher.decrypt(row.encrypted_sandbox_state)
            row.sandbox_dirty, row.sandbox_task_id = True, access_id
            row.sandbox_access_until = now() + timedelta(minutes=2)
            if mutate:
                row.sandbox_generation += 1
        renewal = asyncio.create_task(self._renew(session_id, access_id))
        attempted = False
        clean = False
        try:
            async with factory(context) as client:
                if client.backend_id != backend:
                    raise ConflictError("Sandbox client factory returned the wrong backend")
                parsed = client.deserialize_session_state(state)
                attempted = True
                live = await client.resume(parsed)
                try:
                    await live.start()
                    yield live
                finally:

                    async def persist() -> None:
                        await live.aclose()
                        with self.sessions.begin() as db:
                            current = db.get(AgentSession, session_id, with_for_update=True)
                            if current is None or current.sandbox_task_id != access_id:
                                raise ConflictError("Sandbox management ownership changed")
                            current.encrypted_sandbox_state = self.cipher.encrypt(
                                client.serialize_session_state(live.state)
                            )

                    cleanup = asyncio.create_task(persist())
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        await cleanup
                        clean = True
                        raise
            clean = True
            with self.sessions.begin() as db:
                check(db, self.root, row.owner_id, 0)
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
            with self.sessions.begin() as db:
                current = db.get(AgentSession, session_id, with_for_update=True)
                if current is not None and current.sandbox_task_id == access_id:
                    current.sandbox_access_until = None
                    if not attempted or clean:
                        current.sandbox_dirty = False

    def check_write(self, session_id: str, amount: int) -> None:
        with self.sessions.begin() as db:
            row = db.get(AgentSession, session_id)
            if row is None:
                raise NotFoundError("Session not found")
            check(db, self.root, row.owner_id, amount)
