from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import Session as DbSession

from backend.app.agent_runtime.core.contracts import AgentRuntimeSessionItem
from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

ACTIVE_SESSION_STATUS = "active"
ARCHIVED_SESSION_STATUS = "archived"
FROZEN_SESSION_STATUS = "frozen"
WRITABLE_SESSION_STATUSES = frozenset({ACTIVE_SESSION_STATUS})
PERSISTENT_AGENT_SESSION_STATUSES = frozenset(
    {ACTIVE_SESSION_STATUS, ARCHIVED_SESSION_STATUS, FROZEN_SESSION_STATUS}
)
SESSION_WRITE_MAX_RETRIES = 3


@dataclass(frozen=True)
class PersistentAgentSessionRef:
    session_key: str
    workspace_id: UUID
    scope_type: str
    scope_id: str


class PersistentAgentSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "persistent_agent_sessions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "session_key", name="uq_agent_sessions_workspace_key"),
        Index("ix_agent_sessions_workspace_scope", "workspace_id", "scope_type", "scope_id"),
        Index("ix_agent_sessions_workspace_updated", "workspace_id", "updated_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_key: Mapped[str] = mapped_column(String(240), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(80), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(240), nullable=False)
    agent_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_team_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_teams.id", ondelete="CASCADE"),
        nullable=True,
    )
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=True,
    )
    openai_conversation_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    session_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ACTIVE_SESSION_STATUS)


class PersistentAgentSessionItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "persistent_agent_session_items"
    __table_args__ = (
        UniqueConstraint(
            "persistent_session_id",
            "sequence",
            name="uq_agent_session_items_session_sequence",
        ),
        Index("ix_agent_session_items_workspace_session", "workspace_id", "persistent_session_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    persistent_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("persistent_agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    item: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class SQLAlchemyAgentSession:
    """OpenAI Agents SDK Session backed by the product Postgres database."""

    session_settings = None

    def __init__(
        self,
        *,
        db_session: DbSession,
        ref: PersistentAgentSessionRef,
        agent_profile_id: UUID | None = None,
        agent_team_id: UUID | None = None,
        task_id: UUID | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self._db_session = db_session
        self._ref = ref
        self._agent_profile_id = agent_profile_id
        self._agent_team_id = agent_team_id
        self._task_id = task_id
        self._metadata = metadata or {}
        self.session_id = ref.session_key

    async def get_items(self, limit: int | None = None) -> list[AgentRuntimeSessionItem]:
        return self._get_items_sync(limit)

    async def add_items(self, items: list[AgentRuntimeSessionItem]) -> None:
        if not items:
            return
        self._add_items_sync([_dict_item(item) for item in items])

    async def pop_item(self) -> AgentRuntimeSessionItem | None:
        return self._pop_item_sync()

    async def clear_session(self) -> None:
        self._clear_session_sync()

    def _get_or_create_session(self) -> PersistentAgentSession:
        existing = self._db_session.scalar(
            select(PersistentAgentSession).where(
                PersistentAgentSession.workspace_id == self._ref.workspace_id,
                PersistentAgentSession.session_key == self._ref.session_key,
            )
        )
        if existing is not None:
            return existing

        created = PersistentAgentSession(
            workspace_id=self._ref.workspace_id,
            session_key=self._ref.session_key,
            scope_type=self._ref.scope_type,
            scope_id=self._ref.scope_id,
            agent_profile_id=self._agent_profile_id,
            agent_team_id=self._agent_team_id,
            task_id=self._task_id,
            session_metadata=self._metadata,
        )
        try:
            with self._db_session.begin_nested():
                self._db_session.add(created)
                self._db_session.flush([created])
        except IntegrityError:
            existing = self._db_session.scalar(
                select(PersistentAgentSession).where(
                    PersistentAgentSession.workspace_id == self._ref.workspace_id,
                    PersistentAgentSession.session_key == self._ref.session_key,
                )
            )
            if existing is None:
                raise
            return existing
        return created

    def _get_items_sync(self, limit: int | None) -> list[dict[str, Any]]:
        persistent_session = self._get_or_create_session()
        if persistent_session.status == ARCHIVED_SESSION_STATUS:
            return []
        statement = select(PersistentAgentSessionItem).where(
            PersistentAgentSessionItem.workspace_id == self._ref.workspace_id,
            PersistentAgentSessionItem.persistent_session_id == persistent_session.id,
        )
        if limit is None:
            rows = self._db_session.scalars(
                statement.order_by(PersistentAgentSessionItem.sequence.asc())
            ).all()
        else:
            rows = list(
                self._db_session.scalars(
                    statement.order_by(PersistentAgentSessionItem.sequence.desc()).limit(limit)
                ).all()
            )
            rows.reverse()
        return [_dict_item(row.item) for row in rows]

    def _add_items_sync(self, items: list[dict[str, Any]]) -> None:
        persistent_session = self._get_or_create_session()
        if persistent_session.status not in WRITABLE_SESSION_STATUSES:
            return
        last_error: IntegrityError | None = None
        for _ in range(SESSION_WRITE_MAX_RETRIES):
            self._lock_session(persistent_session.id)
            start_sequence = (
                self._db_session.scalar(
                    select(func.coalesce(func.max(PersistentAgentSessionItem.sequence), 0)).where(
                        PersistentAgentSessionItem.workspace_id == self._ref.workspace_id,
                        PersistentAgentSessionItem.persistent_session_id == persistent_session.id,
                    )
                )
                or 0
            )
            try:
                with self._db_session.begin_nested():
                    for offset, item in enumerate(items, start=1):
                        self._db_session.add(
                            PersistentAgentSessionItem(
                                workspace_id=self._ref.workspace_id,
                                persistent_session_id=persistent_session.id,
                                sequence=start_sequence + offset,
                                item=_dict_item(item),
                            )
                        )
                    persistent_session.updated_at = datetime.now(UTC)
                    self._db_session.flush()
                return
            except IntegrityError as exc:
                if not _is_session_item_sequence_collision(exc):
                    raise
                last_error = exc
                persistent_session = self._get_or_create_session()
        if last_error is not None:
            raise last_error
        raise RuntimeError("Persistent agent session append failed without an integrity error")

    def _lock_session(self, session_id: UUID) -> None:
        locked_session_id = self._db_session.scalar(
            select(PersistentAgentSession.id)
            .where(
                PersistentAgentSession.workspace_id == self._ref.workspace_id,
                PersistentAgentSession.id == session_id,
            )
            .with_for_update()
        )
        if locked_session_id is None:
            raise ValueError("Persistent agent session not found")

    def _latest_item(
        self,
        persistent_session: PersistentAgentSession,
    ) -> PersistentAgentSessionItem | None:
        return self._db_session.scalar(
            select(PersistentAgentSessionItem)
            .where(
                PersistentAgentSessionItem.workspace_id == self._ref.workspace_id,
                PersistentAgentSessionItem.persistent_session_id == persistent_session.id,
            )
            .order_by(PersistentAgentSessionItem.sequence.desc())
            .limit(1)
        )

    def _items_for_session(
        self,
        persistent_session: PersistentAgentSession,
    ) -> list[PersistentAgentSessionItem]:
        return list(
            self._db_session.scalars(
                select(PersistentAgentSessionItem).where(
                    PersistentAgentSessionItem.workspace_id == self._ref.workspace_id,
                    PersistentAgentSessionItem.persistent_session_id == persistent_session.id,
                )
            ).all()
        )

    def _pop_item_sync(self) -> dict[str, Any] | None:
        persistent_session = self._get_or_create_session()
        if persistent_session.status not in WRITABLE_SESSION_STATUSES:
            return None
        self._lock_session(persistent_session.id)
        item = self._latest_item(persistent_session)
        if item is None:
            return None
        payload = _dict_item(item.item)
        self._db_session.delete(item)
        persistent_session.updated_at = datetime.now(UTC)
        self._db_session.flush()
        return payload

    def _clear_session_sync(self) -> None:
        persistent_session = self._get_or_create_session()
        if persistent_session.status not in WRITABLE_SESSION_STATUSES:
            return
        self._lock_session(persistent_session.id)
        items = self._items_for_session(persistent_session)
        for item in items:
            self._db_session.delete(item)
        persistent_session.openai_conversation_id = None
        persistent_session.updated_at = datetime.now(UTC)
        self._db_session.flush()


def _dict_item(item: object) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, dict):
            return dict(dumped)
    return {"value": item}


def _is_session_item_sequence_collision(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    constraint_name = getattr(diag, "constraint_name", None)
    if constraint_name == "uq_agent_session_items_session_sequence":
        return True
    text = str(orig or exc)
    return (
        "uq_agent_session_items_session_sequence" in text
        or "persistent_agent_session_items.persistent_session_id" in text
        or "persistent_session_id, sequence" in text
    )
