"""Persist and scope schedule definitions in short PostgreSQL transactions."""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from opsmesh.plugins.database import Database

from .config import Settings
from .declarations import ScheduleDeclaration
from .errors import ScheduleError, ScheduleNotFound
from .models import ScheduleRecord
from .schemas import ScheduleDefinition, resolve_schedule_definition


def namespace_lock(namespace: str) -> int:
    """Return a stable signed PostgreSQL advisory lock key."""
    return int.from_bytes(
        hashlib.sha256(f"opsmesh.cron:{namespace}".encode()).digest()[:8],
        "big",
        signed=True,
    )


class Schedules:
    """Store JSON definitions; never serialize handlers or scheduler jobs."""

    def __init__(self, database: Database, settings: Settings) -> None:
        """Bind the shared database and this scheduler's namespace."""
        self.database = database
        self.settings = settings

    def _scope(self, owner: str | None = None) -> list[ColumnElement[bool]]:
        conditions = [ScheduleRecord.namespace == self.settings.namespace]
        if owner is not None:
            conditions.append(ScheduleRecord.owner_plugin == owner)
        return conditions

    async def all(self) -> list[ScheduleRecord]:
        """Read this namespace for timer reconciliation."""
        async with self.database.session() as session:
            return list(
                await session.scalars(
                    select(ScheduleRecord).where(*self._scope())
                )
            )

    async def list(
        self, owner: str, *, limit: int = 50, offset: int = 0
    ) -> list[ScheduleRecord]:
        """Return a bounded page in a calling plugin's scope."""
        if not 1 <= limit <= 200 or offset < 0:
            raise ScheduleError("Invalid schedule pagination")
        async with self.database.session() as session:
            return list(
                await session.scalars(
                    select(ScheduleRecord)
                    .where(*self._scope(owner))
                    .order_by(ScheduleRecord.name)
                    .limit(limit)
                    .offset(offset)
                )
            )

    async def get(self, owner: str, schedule_id: str) -> ScheduleRecord:
        """Read one definition without crossing plugin ownership."""
        async with self.database.session() as session:
            record = await session.scalar(
                select(ScheduleRecord).where(
                    *self._scope(owner), ScheduleRecord.id == schedule_id
                )
            )
            if record is None:
                raise ScheduleNotFound("Schedule not found")
            return record

    async def initialize(self, declaration: ScheduleDeclaration) -> None:
        """Insert a code default only when no persistent override exists."""
        if declaration.default is None:
            return
        async with self.database.transaction() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {
                    "key": namespace_lock(
                        self.settings.namespace + ":definitions"
                    )
                },
            )
            existing = await session.scalar(
                select(ScheduleRecord.id).where(
                    *self._scope(declaration.owner),
                    ScheduleRecord.name == declaration.default.name,
                )
            )
            if existing is None:
                await self._insert(
                    session, declaration.owner, declaration.default, "code"
                )

    async def _insert(
        self,
        session: AsyncSession,
        owner: str,
        definition: ScheduleDefinition,
        source: str,
    ) -> ScheduleRecord:
        count = await session.scalar(
            select(func.count())
            .select_from(ScheduleRecord)
            .where(*self._scope())
        )
        if count >= self.settings.max_schedules:
            raise ScheduleError("The schedule limit has been reached")
        now = datetime.now(UTC)
        record = ScheduleRecord(
            id=str(uuid4()),
            namespace=self.settings.namespace,
            owner_plugin=owner,
            name=definition.name,
            definition=definition.model_dump(mode="json"),
            source=source,
            revision=str(uuid4()),
            created_at=now,
            updated_at=now,
            completed=False,
            expired=False,
        )
        session.add(record)
        await session.flush()
        return record

    async def add(
        self, owner: str, definition: ScheduleDefinition
    ) -> ScheduleRecord:
        """Insert a dynamic definition within the namespace limit."""
        definition = resolve_schedule_definition(definition, self.settings)
        async with self.database.transaction() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {
                    "key": namespace_lock(
                        self.settings.namespace + ":definitions"
                    )
                },
            )
            duplicate = await session.scalar(
                select(ScheduleRecord.id).where(
                    *self._scope(owner), ScheduleRecord.name == definition.name
                )
            )
            if duplicate:
                raise ScheduleError("A schedule with this name already exists")
            return await self._insert(session, owner, definition, "dynamic")

    async def update(
        self, owner: str, schedule_id: str, definition: ScheduleDefinition
    ) -> ScheduleRecord:
        """Replace a definition and invalidate previous timer callbacks."""
        definition = resolve_schedule_definition(definition, self.settings)
        async with self.database.transaction() as session:
            record = await session.scalar(
                select(ScheduleRecord)
                .where(*self._scope(owner), ScheduleRecord.id == schedule_id)
                .with_for_update()
            )
            if record is None:
                raise ScheduleNotFound("Schedule not found")
            if record.source == "code":
                if (
                    definition.name != record.name
                    or definition.handler != record.name
                ):
                    raise ScheduleError(
                        "Code schedule names and handlers are fixed "
                        "by the declaration"
                    )
            record.name = definition.name
            record.definition = definition.model_dump(mode="json")
            record.revision = str(uuid4())
            record.completed = record.expired = False
            record.updated_at = datetime.now(UTC)
            return record

    async def set_enabled(
        self, owner: str, schedule_id: str, enabled: bool
    ) -> ScheduleRecord:
        """Change enabled intent without overwriting a concurrent definition."""
        async with self.database.transaction() as session:
            record = await session.scalar(
                select(ScheduleRecord)
                .where(*self._scope(owner), ScheduleRecord.id == schedule_id)
                .with_for_update()
            )
            if record is None:
                raise ScheduleNotFound("Schedule not found")
            definition = resolve_schedule_definition(
                ScheduleDefinition.model_validate(record.definition),
                self.settings,
            )
            record.definition = definition.model_copy(
                update={"enabled": enabled}
            ).model_dump(mode="json")
            record.revision = str(uuid4())
            record.updated_at = datetime.now(UTC)
            return record

    async def delete(self, owner: str, schedule_id: str) -> None:
        """Delete a dynamic schedule without resurrecting code defaults."""
        async with self.database.transaction() as session:
            record = await session.scalar(
                select(ScheduleRecord)
                .where(*self._scope(owner), ScheduleRecord.id == schedule_id)
                .with_for_update()
            )
            if record is None:
                raise ScheduleNotFound("Schedule not found")
            if record.source == "code":
                raise ScheduleError(
                    "Pause code schedules instead of deleting them"
                )
            await session.delete(record)

    async def complete(
        self, schedule_id: str, revision: str, *, expired: bool = False
    ) -> None:
        """Finalize one-shot work only if its definition has not changed."""
        async with self.database.transaction() as session:
            record = await session.scalar(
                select(ScheduleRecord)
                .where(
                    *self._scope(),
                    ScheduleRecord.id == schedule_id,
                    ScheduleRecord.revision == revision,
                )
                .with_for_update()
            )
            if record is not None:
                record.expired = expired
                record.completed = not expired
                record.updated_at = datetime.now(UTC)
