"""PG-transactional namespace state, monotonic CAS and bounded cleanup."""

from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from opsmesh.extensions.models import ExtensionNamespace, ExtensionState
from opsmesh.extensions.schemas import StateMutation
from opsmesh.identity.quotas import service as quotas
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.serialization import canonical_json, payload_digest

MAX_STATE_VALUE_BYTES = 1048576


def get_namespace(
    session: Session,
    *,
    instance_id: UUID,
    principal_id: UUID,
    scope_key: str,
) -> ExtensionNamespace:
    session.execute(
        insert(ExtensionNamespace)
        .values(
            instance_id=instance_id,
            principal_id=principal_id,
            scope_key=scope_key,
        )
        .on_conflict_do_nothing(index_elements=["instance_id", "principal_id", "scope_key"])
    )
    namespace = session.scalar(
        select(ExtensionNamespace).where(
            ExtensionNamespace.instance_id == instance_id,
            ExtensionNamespace.principal_id == principal_id,
            ExtensionNamespace.scope_key == scope_key,
        )
    )
    assert namespace is not None
    return namespace


def lock_namespace(session: Session, namespace_id: UUID) -> ExtensionNamespace:
    namespace = session.get(ExtensionNamespace, namespace_id, with_for_update=True)
    if namespace is None:
        raise NotFoundError("Extension namespace not found")
    return namespace


def apply_mutations(
    session: Session,
    *,
    namespace_id: UUID,
    entries: list[StateMutation],
    limit_bytes: int,
) -> None:
    namespace = lock_namespace(session, namespace_id)
    if len({entry.key for entry in entries}) != len(entries):
        raise InvalidArgumentError("A state batch cannot repeat a key")
    # All expected versions are checked before any mutation. The namespace lock
    # also serializes the aggregate quota and prevents absent-key CAS races.
    changes: list[tuple[StateMutation, ExtensionState | None, int]] = []
    projected = namespace.used_bytes
    for entry in entries:
        row = session.get(ExtensionState, (namespace_id, entry.key))
        version = row.version if row is not None else 0
        if entry.expected_version is not None and entry.expected_version != version:
            raise ConflictError("State version changed", code="state_version_conflict")
        size = len(entry.key.encode()) + len(canonical_json({"value": entry.value}).encode())
        if entry.delete:
            size = len(entry.key.encode())
        if size > MAX_STATE_VALUE_BYTES:
            raise InvalidArgumentError("State value exceeds the byte limit")
        projected += size - (row.size if row else 0)
        changes.append((entry, row, size))
    if projected > limit_bytes:
        raise ConflictError("Extension state quota exceeded", code="state_quota_exceeded")
    for entry, row, size in changes:
        # A tombstone retains the monotonically increasing version, avoiding
        # delete/recreate ABA. Tombstones consume only their bounded key cost.
        namespace.revision += 1
        if row is None:
            row = ExtensionState(namespace_id=namespace_id, key=entry.key)
            session.add(row)
        elif row.storage_reservation_id is not None:
            quotas.reclaim_storage(session, reservation_id=row.storage_reservation_id)
        row.value = {"deleted": entry.delete, "value": None if entry.delete else entry.value}
        row.version = namespace.revision
        row.size = size
        reservation = quotas.reserve_storage(
            session,
            user_id=namespace.principal_id,
            amount=size,
            idempotency_scope="extensions.state",
            idempotency_key=f"{namespace.id}:{namespace.revision}",
        )
        quotas.settle_storage(session, reservation_id=reservation.id, actual_amount=size)
        row.storage_reservation_id = reservation.id
    namespace.used_bytes = projected
    session.flush()


def read_value(session: Session, namespace_id: UUID, key: str) -> dict[str, Any]:
    row = session.get(ExtensionState, (namespace_id, key))
    if row is None:
        return {"value": None, "version": 0, "exists": False}
    value = dict(row.value) if isinstance(row.value, dict) else {}
    return {
        "value": value.get("value"),
        "version": row.version,
        "exists": not value.get("deleted", False),
    }


def list_values(
    session: Session,
    namespace_id: UUID,
    *,
    prefix: str = "",
    cursor: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    if len(prefix) > 512 or len(cursor) > 512 or not 1 <= limit <= 200:
        raise InvalidArgumentError("Invalid state page")
    rows = list(
        session.scalars(
            select(ExtensionState)
            .where(
                ExtensionState.namespace_id == namespace_id,
                ExtensionState.key.startswith(prefix, autoescape=True),
                ExtensionState.key > cursor,
            )
            .order_by(ExtensionState.key)
            .limit(limit + 1)
        )
    )
    entries: list[dict[str, Any]] = []
    size = 0
    for row in rows[:limit]:
        item = {"key": row.key, **read_value(session, namespace_id, row.key)}
        item_size = len(canonical_json(item).encode())
        if entries and size + item_size > 1048576:
            break
        entries.append(item)
        size += item_size
    return {
        "entries": entries,
        "next_cursor": entries[-1]["key"] if len(rows) > len(entries) else None,
    }


def cleanup_preview(session: Session, namespace_id: UUID) -> dict[str, Any]:
    namespace = lock_namespace(session, namespace_id)
    rows = list(
        session.scalars(
            select(ExtensionState)
            .where(
                ExtensionState.namespace_id == namespace_id,
            )
            .order_by(ExtensionState.key)
            .limit(100)
        )
    )
    facts: dict[str, Any] = {
        "namespace_id": str(namespace.id),
        "revision": namespace.revision,
        "used_bytes": namespace.used_bytes,
        "entry_count": session.scalar(
            select(func.count())
            .select_from(ExtensionState)
            .where(ExtensionState.namespace_id == namespace_id)
        ),
        "entries": [{"key": row.key, "version": row.version, "size": row.size} for row in rows],
    }
    return {**facts, "preview_digest": payload_digest(facts)}


def cleanup_namespace(
    session: Session,
    namespace_id: UUID,
    *,
    expected_revision: int,
    preview_digest: str,
) -> None:
    preview = cleanup_preview(session, namespace_id)
    if preview["revision"] != expected_revision or preview["preview_digest"] != preview_digest:
        raise ConflictError("Namespace cleanup preview is stale")
    namespace = lock_namespace(session, namespace_id)
    for row in session.scalars(
        select(ExtensionState).where(
            ExtensionState.namespace_id == namespace_id,
        )
    ):
        if row.storage_reservation_id is not None:
            quotas.reclaim_storage(session, reservation_id=row.storage_reservation_id)
    session.execute(delete(ExtensionState).where(ExtensionState.namespace_id == namespace_id))
    namespace.used_bytes = 0
    namespace.revision += 1
