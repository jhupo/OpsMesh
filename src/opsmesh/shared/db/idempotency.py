"""Generic durable creation receipts scoped by caller, resource owner and operation."""

from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from typing import TypeVar
from uuid import UUID

from sqlalchemy import DateTime, String, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from opsmesh.shared.db.base import Base
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.serialization import payload_digest

T = TypeVar("T")
_MAX_IDEMPOTENCY_KEY_LENGTH = 256


class CreationReceipt(Base):
    __tablename__ = "http_creation_receipts"

    scope_id: Mapped[UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(primary_key=True)
    operation: Mapped[str] = mapped_column(String(256), primary_key=True)
    key_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IdempotencyInProgressError(ConflictError):
    def __init__(self) -> None:
        super().__init__(
            "Request with this Idempotency-Key is still processing", code="idempotency_in_progress"
        )


def run_idempotent_create(
    *,
    session: Session,
    scope_id: UUID,
    actor_id: UUID,
    operation: str,
    idempotency_key: str | None,
    request_payload: dict[str, object],
    get_existing: Callable[[UUID], T | None],
    create: Callable[[], T],
    resource_id: Callable[[T], UUID],
) -> T:
    """Stage creation and its receipt in the caller-owned transaction.

    Callbacks must not commit. Replay must check current access in get_existing.
    The application boundary commits or rolls back all staged work together.
    """
    key = idempotency_key.strip() if idempotency_key is not None else None
    receipt: CreationReceipt | None = None
    try:
        if key:
            if len(key) > _MAX_IDEMPOTENCY_KEY_LENGTH:
                raise ConflictError(
                    f"Idempotency-Key must not exceed {_MAX_IDEMPOTENCY_KEY_LENGTH} characters"
                )
            key_digest = sha256(key.encode()).hexdigest()
            identity = (scope_id, actor_id, operation, key_digest)
            lock_digest = sha256("|".join(map(str, identity)).encode()).digest()
            lock_id = int.from_bytes(lock_digest[:8], "big", signed=True)
            if not session.scalar(select(func.pg_try_advisory_xact_lock(lock_id))):
                raise IdempotencyInProgressError()
            request_digest = payload_digest(request_payload)
            existing = session.get(CreationReceipt, identity)
            if existing is not None:
                if existing.request_digest != request_digest:
                    raise ConflictError(
                        "Idempotency-Key was used with a different request",
                        code="idempotency_key_reused",
                    )
                resource = get_existing(existing.resource_id)
                if resource is None:
                    raise ConflictError(
                        "The resource created by this request no longer exists",
                        code="idempotency_resource_missing",
                    )
                return resource
            receipt = CreationReceipt(
                scope_id=scope_id,
                actor_id=actor_id,
                operation=operation,
                key_digest=key_digest,
                request_digest=request_digest,
            )
        created = create()
        session.flush()
        if receipt is not None:
            receipt.resource_id = resource_id(created)
            session.add(receipt)
        session.flush()
        return created
    except IntegrityError as exc:
        raise ConflictError("Resource creation conflicts with existing data") from exc
