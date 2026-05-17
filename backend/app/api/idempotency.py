from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from redis import Redis

from backend.app.redis.keys import RedisKeyBuilder

PROCESSING_VALUE = "processing"


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
        workspace_id: UUID,
        operation: str,
        idempotency_key: str | None,
    ) -> IdempotencyReservation | None:
        normalized_key = idempotency_key.strip() if idempotency_key is not None else None
        if not normalized_key:
            return None

        storage_key = self._storage_key(workspace_id, operation, normalized_key)
        current_value = self._redis.get(storage_key)
        if current_value is not None:
            if current_value == PROCESSING_VALUE:
                raise IdempotencyInProgressError
            return IdempotencyReservation(
                key=storage_key,
                existing_resource_id=UUID(current_value),
                created=False,
            )

        reserved = self._redis.set(
            storage_key,
            PROCESSING_VALUE,
            nx=True,
            ex=self._ttl_seconds,
        )
        if not reserved:
            raise IdempotencyInProgressError

        return IdempotencyReservation(key=storage_key, existing_resource_id=None, created=True)

    def complete(self, reservation: IdempotencyReservation | None, resource_id: UUID) -> None:
        if reservation is None or not reservation.created:
            return
        self._redis.set(reservation.key, str(resource_id), ex=self._ttl_seconds)

    def release(self, reservation: IdempotencyReservation | None) -> None:
        if reservation is None or not reservation.created:
            return
        self._redis.delete(reservation.key)

    def forget(self, reservation: IdempotencyReservation | None) -> None:
        if reservation is None:
            return
        self._redis.delete(reservation.key)

    def _storage_key(self, workspace_id: UUID, operation: str, idempotency_key: str) -> str:
        return self._keys.idempotency_key(
            str(workspace_id),
            f"http:{operation}:{idempotency_key.strip()}",
        )
