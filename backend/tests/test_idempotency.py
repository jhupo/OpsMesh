from dataclasses import dataclass
from uuid import UUID, uuid4

import fakeredis
import pytest

from backend.app.api.idempotency import (
    IdempotencyInProgressError,
    IdempotencyService,
    run_idempotent_create,
)
from backend.app.redis.keys import RedisKeyBuilder


def test_idempotency_service_uses_structured_states() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    service = IdempotencyService(redis, RedisKeyBuilder("chaincloud"))
    scope_id = uuid4()
    resource_id = uuid4()

    reservation = service.reserve(
        scope_id=scope_id,
        operation="tasks.create",
        idempotency_key="client-key",
    )

    assert reservation is not None
    assert reservation.created is True
    assert '"status":"in_progress"' in redis.get(reservation.key)

    with pytest.raises(IdempotencyInProgressError):
        service.reserve(
            scope_id=scope_id,
            operation="tasks.create",
            idempotency_key="client-key",
        )

    service.complete(reservation, resource_id)
    repeated = service.reserve(
        scope_id=scope_id,
        operation="tasks.create",
        idempotency_key="client-key",
    )

    assert repeated is not None
    assert repeated.created is False
    assert repeated.existing_resource_id == resource_id
    assert '"status":"succeeded"' in redis.get(reservation.key)


def test_idempotency_service_marks_failed_reservations_with_short_ttl() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    service = IdempotencyService(redis, RedisKeyBuilder("chaincloud"), ttl_seconds=86_400)
    reservation = service.reserve(
        scope_id=uuid4(),
        operation="tasks.create",
        idempotency_key="client-key",
    )

    service.release(reservation)

    assert reservation is not None
    assert '"status":"failed"' in redis.get(reservation.key)
    assert 0 < redis.ttl(reservation.key) <= 300


def test_failed_reservation_retry_records_success_for_later_deduplication() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    service = IdempotencyService(redis, RedisKeyBuilder("chaincloud"))
    scope_id = uuid4()
    resources: dict[UUID, _Resource] = {}
    failed_once = False

    def create_resource() -> _Resource:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("temporary failure")
        resource = _Resource(id=uuid4())
        resources[resource.id] = resource
        return resource

    with pytest.raises(RuntimeError, match="temporary failure"):
        run_idempotent_create(
            idempotency=service,
            scope_id=scope_id,
            operation="tasks.create",
            idempotency_key="retry-key",
            get_existing=resources.get,
            create=create_resource,
            resource_id=lambda resource: resource.id,
        )

    created = run_idempotent_create(
        idempotency=service,
        scope_id=scope_id,
        operation="tasks.create",
        idempotency_key="retry-key",
        get_existing=resources.get,
        create=create_resource,
        resource_id=lambda resource: resource.id,
    )
    repeated = run_idempotent_create(
        idempotency=service,
        scope_id=scope_id,
        operation="tasks.create",
        idempotency_key="retry-key",
        get_existing=resources.get,
        create=create_resource,
        resource_id=lambda resource: resource.id,
    )

    assert repeated == created
    assert len(resources) == 1


def test_idempotency_service_supports_legacy_values() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    keys = RedisKeyBuilder("chaincloud")
    service = IdempotencyService(redis, keys)
    scope_id = uuid4()
    resource_id = uuid4()
    storage_key = keys.idempotency_key(
        str(scope_id),
        "http:tasks.create:legacy",
    )
    redis.set(storage_key, str(resource_id))

    repeated = service.reserve(
        scope_id=scope_id,
        operation="tasks.create",
        idempotency_key="legacy",
    )

    assert repeated is not None
    assert repeated.created is False
    assert repeated.existing_resource_id == resource_id


@dataclass(frozen=True)
class _Resource:
    id: UUID
