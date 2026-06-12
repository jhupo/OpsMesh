from uuid import UUID, uuid4

import fakeredis
import pytest

from backend.app.core.trace_context import TraceContext, trace_context
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue, consume_once


def test_key_builder_scopes_workspace_keys() -> None:
    keys = RedisKeyBuilder(prefix="opsmesh")

    assert keys.workspace_queue("workspace-1", "agent_runs") == (
        "opsmesh:workspace:workspace-1:queue:agent_runs"
    )
    assert keys.run_lock("workspace-1", "run-1") == "opsmesh:lock:workspace-1:run:run-1"


def test_enqueue_is_idempotent_and_dequeue_round_trips_payload() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("opsmesh"), queue_name="agent_runs")
    job = _job()

    assert queue.enqueue(job) is True
    assert queue.enqueue(job) is False

    stored = queue.dequeue()

    assert stored == job
    assert queue.count_processing() == 1
    assert queue.ack(job) is True
    assert queue.count_processing() == 0
    assert queue.dequeue() is None


def test_enqueue_propagates_current_trace_context_to_job_payload() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("opsmesh"), queue_name="agent_runs")
    parent = TraceContext(
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="abcdef0123456789",
    )

    with trace_context(parent):
        assert queue.enqueue(_job()) is True

    stored = queue.dequeue()

    assert stored is not None
    assert stored.trace_id == parent.trace_id
    assert stored.parent_span_id == parent.span_id
    assert stored.span_id is not None
    assert stored.span_id != parent.span_id


def test_dequeue_leases_job_until_processing_reclaim() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(
        redis=redis,
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
        visibility_timeout_seconds=1,
    )
    job = _job()
    queue.enqueue(job)

    leased = queue.dequeue()

    assert leased == job
    assert queue.count_queued() == 0
    assert queue.count_processing() == 1
    assert queue.dequeue() is None

    reclaimed = queue.reclaim_expired(now=9_999_999_999)

    assert reclaimed == [job]
    assert queue.count_processing() == 0
    assert queue.count_queued() == 1
    assert queue.dequeue() == job


def test_force_enqueue_preserves_retry_override_behavior() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("opsmesh"), queue_name="agent_runs")
    job = _job()

    assert queue.enqueue(job) is True
    assert queue.enqueue(job, force=True) is True

    assert queue.dequeue() == job
    assert queue.dequeue() == job
    assert queue.dequeue() is None


def test_dequeue_matching_skips_unmatched_head_job_without_dropping_it() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("opsmesh"), queue_name="agent_runs")
    docker_job = _job(routing={"runtime_modes": ["docker"]})
    self_hosted_job = _job(routing={"runtime_modes": ["self_hosted"]})
    queue.enqueue(docker_job)
    queue.enqueue(self_hosted_job)

    matched = queue.dequeue_matching(
        lambda job: job.routing.get("runtime_modes") == ["self_hosted"],
    )

    assert matched == self_hosted_job
    assert queue.dequeue() == docker_job
    assert queue.dequeue() is None


def test_blocking_dequeue_matching_does_not_fallback_to_incompatible_job() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(
        redis=redis,
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
        blocking_timeout_seconds=1,
    )
    docker_job = _job(routing={"runtime_modes": ["docker"]})
    queue.enqueue(docker_job)

    matched = queue.dequeue_matching(
        lambda job: job.routing.get("runtime_modes") == ["self_hosted"],
    )

    assert matched is None
    assert queue.dequeue() == docker_job
    assert queue.dequeue() is None


def test_dequeue_selects_highest_priority_job_and_preserves_fifo_ties() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("opsmesh"), queue_name="agent_runs")
    low = _job(priority=1)
    first_high = _job(priority=42)
    second_high = _job(priority=42)

    assert queue.enqueue(low) is True
    assert queue.enqueue(first_high) is True
    assert queue.enqueue(second_high) is True

    assert queue.dequeue() == first_high
    assert queue.dequeue() == second_high
    assert queue.dequeue() == low


def test_peek_returns_jobs_without_removing_them() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("opsmesh"), queue_name="agent_runs")
    first = _job(priority=1)
    second = _job(priority=2)
    queue.enqueue(first)
    queue.enqueue(second)

    peeked = queue.peek(limit=1)

    assert peeked == [first]
    assert queue.count_queued() == 2
    assert queue.dequeue() == second


def test_dequeue_matching_selects_highest_priority_compatible_job() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("opsmesh"), queue_name="agent_runs")
    low = _job(priority=1, routing={"runtime_modes": ["self_hosted"]})
    incompatible = _job(priority=99, routing={"runtime_modes": ["docker"]})
    high = _job(priority=10, routing={"runtime_modes": ["self_hosted"]})
    queue.enqueue(low)
    queue.enqueue(incompatible)
    queue.enqueue(high)

    matched = queue.dequeue_matching(
        lambda job: job.routing.get("runtime_modes") == ["self_hosted"],
    )

    assert matched == high
    assert queue.dequeue() == incompatible
    assert queue.dequeue() == low


def test_run_lock_allows_one_holder() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("opsmesh"), queue_name="agent_runs")

    with (
        queue.run_lock("workspace-1", "run-1") as first_lock,
        queue.run_lock("workspace-1", "run-1") as second_lock,
    ):
        assert first_lock is True
        assert second_lock is False

    with queue.run_lock("workspace-1", "run-1") as next_lock:
        assert next_lock is True


def test_consume_once_requeues_failed_job_then_dead_letters() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    keys = RedisKeyBuilder("opsmesh")
    queue = RedisQueue(redis=redis, keys=keys, queue_name="agent_runs")
    job = _job(max_attempts=2)
    queue.enqueue(job)

    with pytest.raises(RuntimeError, match="boom"):
        consume_once(queue, lambda _: (_ for _ in ()).throw(RuntimeError("boom")))

    retry = queue.dequeue()
    assert retry is not None
    assert retry.attempt == 1

    queue.retry_or_dead_letter(retry)
    raw_dead_letter = redis.lpop(keys.dead_letter_queue("agent_runs"))
    assert raw_dead_letter is not None
    dead_letter = JobPayload.model_validate_json(raw_dead_letter)
    assert dead_letter.attempt == 2
    assert dead_letter.last_error is None


def test_retry_can_be_delayed_and_reclaimed_with_error_metadata() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("opsmesh"), queue_name="agent_runs")
    job = _job(max_attempts=2)

    queue.retry_or_dead_letter(
        job,
        error=RuntimeError("temporary boom"),
        delay_seconds=30,
        now=100,
    )

    assert queue.count_queued() == 0
    assert queue.count_scheduled_retries() == 1
    assert queue.reclaim_due_retries(now=129) == []

    reclaimed = queue.reclaim_due_retries(now=130)

    assert len(reclaimed) == 1
    assert reclaimed[0].attempt == 1
    assert reclaimed[0].last_error == "temporary boom"
    assert reclaimed[0].last_error_type == "RuntimeError"
    assert reclaimed[0].last_failed_at is not None
    assert queue.count_scheduled_retries() == 0
    assert queue.dequeue() == reclaimed[0]


def test_consume_once_acks_successful_job() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("opsmesh"), queue_name="agent_runs")
    job = _job()
    handled: list[JobPayload] = []
    queue.enqueue(job)

    assert consume_once(queue, handled.append) is True

    assert handled == [job]
    assert queue.count_queued() == 0
    assert queue.count_processing() == 0


def test_dead_letter_jobs_can_be_listed_and_requeued() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    keys = RedisKeyBuilder("opsmesh")
    queue = RedisQueue(redis=redis, keys=keys, queue_name="agent_runs")
    job = _job(max_attempts=1)
    queue.retry_or_dead_letter(job)

    dead_letters = queue.list_dead_letters()
    requeued = queue.requeue_dead_letter(dead_letters[0].job_id)

    assert len(dead_letters) == 1
    assert requeued is not None
    assert requeued.job_id != job.job_id
    assert requeued.attempt == 0
    assert queue.list_dead_letters() == []
    assert queue.dequeue() == requeued


def test_queue_list_views_filter_before_applying_limit() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("opsmesh"), queue_name="agent_runs")
    workspace_id = uuid4()
    target_resource_id = uuid4()
    other_resource_id = uuid4()
    other_queued = _job(
        workspace_id=workspace_id,
        resource_id=other_resource_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
    )
    target_queued = _job(
        workspace_id=workspace_id,
        resource_id=target_resource_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
    )
    other_retry = _job(
        workspace_id=workspace_id,
        resource_id=other_resource_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        max_attempts=2,
    )
    target_retry = _job(
        workspace_id=workspace_id,
        resource_id=target_resource_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        max_attempts=2,
    )
    other_dead = _job(
        workspace_id=workspace_id,
        resource_id=other_resource_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        max_attempts=1,
    )
    target_dead = _job(
        workspace_id=workspace_id,
        resource_id=target_resource_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        max_attempts=1,
    )

    queue.enqueue(other_queued)
    queue.enqueue(target_queued)
    queue.retry_or_dead_letter(other_retry, delay_seconds=60, now=100)
    queue.retry_or_dead_letter(target_retry, delay_seconds=60, now=100)
    queue.retry_or_dead_letter(other_dead)
    queue.retry_or_dead_letter(target_dead)

    assert queue.list_queued(
        1,
        workspace_id=workspace_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=target_resource_id,
    ) == [target_queued]
    assert queue.list_scheduled_retries(
        1,
        workspace_id=workspace_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=target_resource_id,
    ) == [target_retry.next_attempt()]
    assert queue.list_dead_letters(
        1,
        workspace_id=workspace_id,
        job_type=JobType.TEAM_EXECUTION_LOOP,
        resource_id=target_resource_id,
    ) == [target_dead.next_attempt()]


def _job(
    max_attempts: int = 3,
    routing: dict[str, object] | None = None,
    priority: int = 0,
    workspace_id: UUID | None = None,
    resource_id: UUID | None = None,
    job_type: JobType = JobType.AGENT_RUN,
) -> JobPayload:
    workspace_id = workspace_id or uuid4()
    resource_id = resource_id or uuid4()
    return JobPayload(
        workspace_id=workspace_id,
        job_type=job_type,
        resource_id=resource_id,
        idempotency_key=f"{job_type}:{workspace_id}:{resource_id}",
        max_attempts=max_attempts,
        routing=routing or {},
        priority=priority,
    )
