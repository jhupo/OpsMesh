"""Run and stream queries over atomically maintained, indexed record projections."""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Float, Index, Integer, String, and_, func, or_, select

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, StoredRecord, VersionedRecordStore
from opsmesh.infrastructure.database.transactions import database_connection
from opsmesh.modules.runtime.api import AgentRunView


class RunRecordStore(VersionedRecordStore):
    def __init__(self, database: AsyncDatabase) -> None:
        super().__init__(
            database,
            "runtime_runs",
            projections={
                "status": (("status",), String(32)),
                "principal_id": (("principal_id",), String(255)),
                "session_id": (("session_id",), String(255)),
                "lease_expires": (("lease_expires",), Float()),
            },
        )
        if not any(index.name == "ix_runtime_runs_status" for index in self.table.indexes):
            Index(
                "ix_runtime_runs_status",
                self.table.c.status,
                self.table.c.created_at,
                self.table.c.id,
            )
            Index("ix_runtime_runs_principal", self.table.c.principal_id, self.table.c.session_id)
            Index("ix_runtime_runs_lease", self.table.c.status, self.table.c.lease_expires)

    async def require(self, run_id: str) -> StoredRecord:
        record = await self.get(run_id)
        if record is None:
            raise OpsMeshError("NOT_FOUND", "Run does not exist")
        return record

    async def select_runs(
        self,
        *,
        statuses: Sequence[str] = (),
        principal_id: str | None = None,
        session_id: str | None = None,
        expired_before: float | None = None,
        limit: int = 1000,
        offset: int = 0,
        after_id: str | None = None,
    ) -> list[StoredRecord]:
        self._ready()
        if not 1 <= limit <= 1000 or offset < 0 or (after_id is not None and offset):
            raise OpsMeshError("INVALID_REQUEST", "Invalid Run query pagination")
        query = select(self.table)
        if after_id is not None:
            anchor = await self.require(after_id)
            query = query.where(
                or_(
                    self.table.c.created_at > anchor.created_at,
                    and_(self.table.c.created_at == anchor.created_at, self.table.c.id > anchor.id),
                )
            )
        if statuses:
            query = query.where(self.table.c.status.in_(statuses))
        if principal_id is not None:
            query = query.where(self.table.c.principal_id == principal_id)
        if session_id is not None:
            query = query.where(self.table.c.session_id == session_id)
        if expired_before is not None:
            query = query.where(func.coalesce(self.table.c.lease_expires, 0) <= expired_before)
        query = query.order_by(self.table.c.created_at, self.table.c.id).limit(limit).offset(offset)
        async with database_connection(self.database.engine) as connection:
            return [self._record(row) for row in (await connection.execute(query)).mappings()]

    async def count_statuses(self, statuses: Sequence[str]) -> int:
        self._ready()
        async with database_connection(self.database.engine) as connection:
            return int(
                await connection.scalar(
                    select(func.count())
                    .select_from(self.table)
                    .where(self.table.c.status.in_(statuses))
                )
                or 0
            )


class RunStreamStore(VersionedRecordStore):
    def __init__(self, database: AsyncDatabase) -> None:
        super().__init__(
            database,
            "runtime_stream",
            projections={
                "run_id": (("run_id",), String(255)),
                "seq": (("seq",), Integer()),
            },
        )
        if not any(index.name == "ix_runtime_stream_cursor" for index in self.table.indexes):
            Index("ix_runtime_stream_cursor", self.table.c.run_id, self.table.c.seq)

    async def read(self, run_id: str, after_seq: int, *, limit: int = 128) -> list[StoredRecord]:
        self._ready()
        if not 1 <= limit <= 1000 or after_seq < 0:
            raise OpsMeshError("INVALID_REQUEST", "Invalid stream cursor or page size")
        query = (
            select(self.table)
            .where(self.table.c.run_id == run_id, self.table.c.seq > after_seq)
            .order_by(self.table.c.seq)
            .limit(limit)
        )
        async with database_connection(self.database.engine) as connection:
            return [self._record(row) for row in (await connection.execute(query)).mappings()]


def run_view(record: StoredRecord) -> AgentRunView:
    data = record.data
    return AgentRunView(
        id=record.id,
        revision=record.revision,
        principal_id=data["principal_id"],
        session_id=data["session_id"],
        agent=data["agent"],
        published_agent_ref=data.get("published_agent_ref", ""),
        published_agent_fingerprint=data.get("published_agent_fingerprint", ""),
        effective_agent_fingerprint=data.get("effective_agent_fingerprint", ""),
        overrides=data.get("overrides", {}),
        status=data["status"],
        attempt_id=data.get("attempt_id"),
        attempt=data.get("attempt", 0),
        output=data.get("output"),
        usage=data.get("usage", {}),
        approvals=data.get("approvals", []),
        approval_policy=data.get("approval_policy", {}),
        approval_requested_at=datetime.fromtimestamp(data["approval_wait_started_at"], UTC)
        if data.get("approval_wait_started_at") is not None
        else None,
        approval_reviews=data.get("approval_reviews", {}),
        has_checkpoint=data.get("checkpoint") is not None,
        has_final_outcome=data.get("final_outcome") is not None,
        cancel_requested=data.get("cancel_requested", False),
        error=data.get("error"),
        metadata=data.get("metadata", {}),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
