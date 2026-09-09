from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar
from uuid import UUID

from redis import Redis

from backend.app.redis.keys import RedisKeyBuilder

STATE_IN_PROGRESS = "in_progress"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
T = TypeVar("T")

_REPLACE_STALE_RESERVATION_SCRIPT = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    redis.call("SET", KEYS[1], ARGV[2], "EX", ARGV[3])
    return 1
end
return 0
"""


class IdempotencyInProgressError(Exception):
    pass


@dataclass(frozen=True)
class IdempotencyReservation:
    key: str
    existing_resource_id: UUID | None
    created: bool


class IdempotencyService:
    def __init__(
        self,
        redis: Redis[str],
        keys: RedisKeyBuilder,
        *,
        ttl_seconds: int = 86_400,
    ) -> None:
        self._redis = redis
        self._keys = keys
        self._ttl_seconds = ttl_seconds

    def reserve(
        self,
        *,
        scope_id: UUID | None = None,
        workspace_id: UUID | None = None,
        operation: str,
        idempotency_key: str | None,
    ) -> IdempotencyReservation | None:
        normalized_key = idempotency_key.strip() if idempotency_key is not None else None
        if not normalized_key:
            return None

        resolved_scope_id = scope_id or workspace_id
        if resolved_scope_id is None:
            raise ValueError("Idempotency scope is required")
        storage_key = self._storage_key(resolved_scope_id, operation, normalized_key)
        current_value = self._redis.get(storage_key)
        if current_value is not None:
            current_state = _decode_idempotency_state(current_value)
            if current_state["status"] == STATE_IN_PROGRESS:
                raise IdempotencyInProgressError
            resource_id = current_state.get("resource_id")
            if not isinstance(resource_id, str):
                in_progress_state = _encode_idempotency_state(status=STATE_IN_PROGRESS)
                replaced = self._redis.eval(  # type: ignore[no-untyped-call]
                    _REPLACE_STALE_RESERVATION_SCRIPT,
                    1,
                    storage_key,
                    current_value,
                    in_progress_state,
                    str(self._ttl_seconds),
                )
                if not replaced:
                    raise IdempotencyInProgressError
                return IdempotencyReservation(
                    key=storage_key,
                    existing_resource_id=None,
                    created=True,
                )
            return IdempotencyReservation(
                key=storage_key,
                existing_resource_id=UUID(resource_id),
                created=False,
            )

        reserved = self._redis.set(
            storage_key,
            _encode_idempotency_state(status=STATE_IN_PROGRESS),
            nx=True,
            ex=self._ttl_seconds,
        )
        if not reserved:
            raise IdempotencyInProgressError

        return IdempotencyReservation(key=storage_key, existing_resource_id=None, created=True)

    def complete(self, reservation: IdempotencyReservation | None, resource_id: UUID) -> None:
        if reservation is None or not reservation.created:
            return
        self._redis.set(
            reservation.key,
            _encode_idempotency_state(
                status=STATE_SUCCEEDED,
                resource_id=str(resource_id),
            ),
            ex=self._ttl_seconds,
        )

    def release(self, reservation: IdempotencyReservation | None) -> None:
        if reservation is None or not reservation.created:
            return
        self._redis.set(
            reservation.key,
            _encode_idempotency_state(status=STATE_FAILED),
            ex=min(self._ttl_seconds, 300),
        )

    def forget(self, reservation: IdempotencyReservation | None) -> None:
        if reservation is None:
            return
        self._redis.delete(reservation.key)

    def _storage_key(self, scope_id: UUID, operation: str, idempotency_key: str) -> str:
        return self._keys.idempotency_key(
            str(scope_id),
            f"http:{operation}:{idempotency_key.strip()}",
        )

def _encode_idempotency_state(
    *,
    status: str,
    resource_id: str | None = None,
) -> str:
    payload = {"status": status}
    if resource_id is not None:
        payload["resource_id"] = resource_id
    return json.dumps(payload, separators=(",", ":"))


def _decode_idempotency_state(raw_value: str) -> dict[str, object]:
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError:
        return {"status": STATE_FAILED}
    if not isinstance(payload, dict):
        return {"status": STATE_FAILED}
    status = payload.get("status")
    if status not in {STATE_IN_PROGRESS, STATE_SUCCEEDED, STATE_FAILED}:
        return {"status": STATE_FAILED}
    return payload


def run_idempotent_create(
    *,
    idempotency: IdempotencyService,
    scope_id: UUID,
    operation: str,
    idempotency_key: str | None,
    get_existing: Callable[[UUID], T | None],
    create: Callable[[], T],
    resource_id: Callable[[T], UUID],
) -> T:
    try:
        reservation = idempotency.reserve(
            scope_id=scope_id,
            operation=operation,
            idempotency_key=idempotency_key,
        )
    except IdempotencyInProgressError:
        raise

    if reservation is not None and reservation.existing_resource_id is not None:
        existing = get_existing(reservation.existing_resource_id)
        if existing is not None:
            return existing
        idempotency.forget(reservation)
        reservation = None

    try:
        created = create()
        idempotency.complete(reservation, resource_id(created))
        return created
    except Exception:
        idempotency.release(reservation)
        raise
