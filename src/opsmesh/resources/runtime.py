from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from agents import Model, ModelProvider
from agents.models.multi_provider import MultiProviderMap
from agents.models.openai_provider import OpenAIProvider
from openai import AsyncOpenAI, DefaultAsyncHttpx2Client
from openai.types.shared import Reasoning
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.infra.database import new_id, now
from opsmesh.infra.encryption import SecretCipher
from opsmesh.quotas.config import ModelDefaults
from opsmesh.quotas.models import ModelConnection
from opsmesh.quotas.storage import check
from opsmesh.quotas.tokens import MeteredMultiProvider, MeteredProvider
from opsmesh.shared.errors import ForbiddenError, NotFoundError
from opsmesh.users.service import identity

from .models import Artifact, Workspace
from .workspaces import directory, safe_path


class InvocationResources:
    def __init__(
        self,
        sessions: sessionmaker,
        cipher: SecretCipher,
        root: Path,
        snapshot: dict[str, Any],
        caller_id: str,
        task_id: str,
        stack: AsyncExitStack,
        model_defaults: ModelDefaults,
    ):
        self.sessions, self.cipher, self.root = sessions, cipher, root
        self.owner_id, self.caller_id, self.task_id = snapshot["owner_id"], caller_id, task_id
        self.stack = stack
        self.model_defaults = model_defaults
        self.data_dir = root / "users" / self.owner_id / "agents" / snapshot["id"]
        self.workspace: Path | None = None
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if snapshot.get("workspace_id"):
            with sessions.begin() as db:
                workspace = db.get(Workspace, snapshot["workspace_id"])
                if workspace is None or workspace.owner_id != self.owner_id:
                    raise NotFoundError("Agent workspace unavailable")
                self.workspace = directory(root, workspace)
                self.workspace.mkdir(parents=True, exist_ok=True)

    async def _provider(self, connection_id: str | None, reserve_tokens: int) -> ModelProvider:
        if not 1 <= reserve_tokens <= 1_000_000:
            raise ValueError("Token reservation must be between 1 and 1000000")

        def authorize(db: Session) -> None:
            identity(db, self.caller_id)
            identity(db, self.owner_id)
            if connection_id is None:
                return
            current = db.get(ModelConnection, connection_id)
            if (
                current is None
                or not current.enabled
                or current.owner_id != self.owner_id
                or (current.expires_at is not None and current.expires_at <= now())
            ):
                raise NotFoundError("Model connection unavailable")

        with self.sessions.begin() as db:
            identity(db, self.caller_id)
            if connection_id is None:
                row = db.scalar(
                    select(ModelConnection).where(
                        ModelConnection.owner_id == self.owner_id,
                        ModelConnection.is_default.is_(True),
                        ModelConnection.enabled.is_(True),
                    )
                )
                if row is not None:
                    connection_id = row.id
            else:
                row = db.get(ModelConnection, connection_id)
            if connection_id is not None and (
                row is None
                or not row.enabled
                or row.owner_id != self.owner_id
                or (row.expires_at is not None and row.expires_at <= now())
            ):
                raise NotFoundError("Model connection unavailable")
            defaults = self.model_defaults
            key = self.cipher.decrypt(row.encrypted_key) if row is not None else defaults.api_key
            model_name = row.model_name if row is not None else defaults.name
            effort = row.reasoning_effort if row is not None else defaults.reasoning_effort
            if not key:
                raise NotFoundError("No default model connection is configured")
            client = AsyncOpenAI(
                api_key=key,
                base_url=row.base_url if row is not None else defaults.base_url,
                max_retries=0,
                http_client=DefaultAsyncHttpx2Client(follow_redirects=False, trust_env=False),
            )
            await self.stack.enter_async_context(client)
            provider = OpenAIProvider(
                openai_client=client,
                use_responses=(row.api_mode if row is not None else defaults.api_mode)
                == "responses",
            )
            return MeteredProvider(
                provider,
                self.sessions,
                self.caller_id,
                self.task_id,
                reserve_tokens,
                authorize,
                default_model=model_name,
                reasoning=Reasoning.model_validate({"effort": effort}) if effort else None,
            )

    async def model(
        self,
        connection_id: str | None = None,
        model_name: str | None = None,
        *,
        reserve_tokens: int = 8192,
    ) -> Model:
        return (await self._provider(connection_id, reserve_tokens)).get_model(model_name)

    async def model_provider(
        self, connections: dict[str, str], *, reserve_tokens: int = 8192
    ) -> ModelProvider:
        mapping = MultiProviderMap()
        for prefix, connection_id in connections.items():
            mapping.add_provider(prefix, await self._provider(connection_id, reserve_tokens))
        return MeteredMultiProvider(mapping, frozenset(connections))

    async def artifact(self, path: str, *, name: str | None = None) -> str:
        if self.workspace is None:
            raise ForbiddenError("This agent has no workspace")
        source = safe_path(self.workspace, path)
        if not source.is_file():
            raise NotFoundError("Artifact source not found")
        filename = name or source.name
        if not filename or len(filename) > 200 or "/" in filename or "\\" in filename:
            raise ValueError("Invalid artifact name")
        with self.sessions.begin() as db:
            size = source.stat().st_size
            check(db, self.root, self.caller_id, size)
            row = Artifact(
                id=new_id(),
                owner_id=self.caller_id,
                task_id=self.task_id,
                name=filename,
                size=size,
            )
            target = self.root / "users" / self.caller_id / "artifacts" / row.id
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with source.open("rb") as incoming, target.open("xb") as outgoing:
                    remaining = size
                    while remaining:
                        block = incoming.read(min(remaining, 1024 * 1024))
                        if not block:
                            raise ValueError("Artifact source changed during export")
                        outgoing.write(block)
                        remaining -= len(block)
                    if incoming.read(1):
                        raise ValueError("Artifact source changed during export")
                db.add(row)
                db.flush()
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            return row.id
