import asyncio
import importlib.metadata
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from agents import SQLiteSession
from agents.extensions.memory.encrypt_session import EncryptedSession
from agents.sandbox import LocalSnapshot, Manifest, SandboxArchiveLimits, SandboxRunConfig
from agents.sandbox.session.sandbox_client import BaseSandboxClient
from opsmesh_sdk.sessions import SessionContext
from sqlalchemy.orm import sessionmaker

from opsmesh.infra.encryption import SecretCipher
from opsmesh.quotas.storage import check
from opsmesh.shared.errors import ConflictError

from .layout import default_manifest, session_directory
from .models import AgentSession


class SessionRuntime:
    def __init__(
        self,
        sessions: sessionmaker,
        cipher: SecretCipher,
        root: Path,
        row: AgentSession,
        task_id: str,
        validate: Callable[..., Any],
        sandbox_root: str,
        plugin_id: str,
        config: dict[str, Any],
        backends: frozenset[str],
    ):
        self.sessions, self.cipher, self.root, self.row = sessions, cipher, root, row
        self.task_id, self.validate, self.sandbox_root = task_id, validate, sandbox_root
        self.plugin_id, self.config, self.backends = plugin_id, config, backends
        self.directory = session_directory(root, row)
        (self.directory / ".opsmesh/session").mkdir(parents=True, exist_ok=True)
        self.sandbox_open = False

    @asynccontextmanager
    async def sandbox(
        self,
        client: BaseSandboxClient[Any],
        options: Any,
        *,
        manifest: Manifest | None = None,
    ) -> AsyncIterator[SandboxRunConfig]:
        if self.sandbox_open:
            raise ConflictError("A session can only open one sandbox at a time")
        if client.backend_id not in self.backends:
            raise ConflictError("Register a sandbox client factory for this backend in setup(ctx)")
        self.sandbox_open = True
        live = None
        try:
            with self.sessions.begin() as db:
                self.validate(db)
                row = db.get(AgentSession, self.row.id, with_for_update=True)
                assert row is not None
                version = importlib.metadata.version("openai-agents")
                if row.sandbox_dirty and row.sandbox_task_id != self.task_id:
                    raise ConflictError(
                        "Sandbox state is uncertain; resume its original task or close this session"
                    )
                if row.sandbox_backend and (
                    row.sandbox_backend != client.backend_id or row.sandbox_sdk_version != version
                ):
                    raise ConflictError("Sandbox backend or SDK version changed")
                saved = (
                    self.cipher.decrypt(row.encrypted_sandbox_state)
                    if row.encrypted_sandbox_state
                    else None
                )
                if row.sandbox_dirty and saved is None:
                    raise ConflictError(
                        "Sandbox initialization was interrupted without recoverable state; close this session"
                    )
                row.sandbox_dirty, row.sandbox_task_id = True, self.task_id
                row.sandbox_generation += 1
                row.sandbox_backend, row.sandbox_sdk_version = client.backend_id, version
                if row.sandbox_plugin_id is None:
                    row.sandbox_plugin_id = self.plugin_id
                    row.encrypted_sandbox_config = self.cipher.encrypt(self.config)
            if saved is not None:
                live = await client.resume(client.deserialize_session_state(saved))
            else:
                initial = default_manifest(self.sandbox_root, manifest)
                live = await client.create(
                    options=options,
                    manifest=initial,
                    snapshot=LocalSnapshot(
                        id=self.row.id,
                        base_path=self.directory / ".opsmesh" / "session" / "snapshots",
                    ),
                )
            await live.start()
            self.save_state(client.serialize_session_state(live.state), clean=False)
            yield SandboxRunConfig(
                session=live, cwd=self.sandbox_root, archive_limits=SandboxArchiveLimits()
            )
        finally:
            try:
                if live is not None:

                    async def persist() -> None:
                        await live.aclose()
                        state = client.serialize_session_state(live.state)
                        self.save_state(state, clean=True, cleanup=True)

                    cleanup = asyncio.create_task(persist())
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        await cleanup
                        raise
            finally:
                self.sandbox_open = False

    def save_state(self, state: dict[str, Any], *, clean: bool, cleanup: bool = False) -> None:
        with self.sessions.begin() as db:
            self.validate(db, cleanup=cleanup)
            row = db.get(AgentSession, self.row.id, with_for_update=True)
            assert row is not None
            row.encrypted_sandbox_state = self.cipher.encrypt(state)
            row.sandbox_dirty = not clean
        if clean:
            with self.sessions.begin() as db:
                check(db, self.root, self.row.owner_id, 0)


def invocation_session(
    sessions: sessionmaker,
    cipher: SecretCipher,
    root: Path,
    row: AgentSession,
    task_id: str,
    stack: AsyncExitStack,
    *,
    encryption_key: str,
    ttl: int,
    sandbox_root: str,
    validate: Callable[..., Any],
    plugin_id: str,
    config: dict[str, Any],
    backends: frozenset[str],
) -> SessionContext:
    runtime = SessionRuntime(
        sessions,
        cipher,
        root,
        row,
        task_id,
        validate,
        sandbox_root,
        plugin_id,
        config,
        backends,
    )
    underlying = SQLiteSession(
        row.id, runtime.directory / ".opsmesh" / "session" / "history.sqlite"
    )
    stack.callback(underlying.close)
    history = EncryptedSession(row.id, underlying, encryption_key, ttl=ttl)
    return SessionContext(
        row.id, row.scope, runtime.directory, sandbox_root, history, runtime.sandbox
    )
