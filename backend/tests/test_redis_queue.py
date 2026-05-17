from uuid import uuid4

import fakeredis
import pytest

from backend.app.redis.keys import RedisKeyBuilder
from backend.app.workers.jobs import JobPayload, JobType
from backend.app.workers.queue import RedisQueue, consume_once


def test_key_builder_scopes_workspace_keys() -> None:
    keys = RedisKeyBuilder(prefix="chaincloud")

    assert keys.workspace_queue("workspace-1", "agent_runs") == (
        "chaincloud:workspace:workspace-1:queue:agent_runs"
    )
    assert keys.run_lock("workspace-1", "run-1") == "chaincloud:lock:workspace-1:run:run-1"


def test_enqueue_is_idempotent_and_dequeue_round_trips_payload() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("chaincloud"), queue_name="agent_runs")
    job = _job()

    assert queue.enqueue(job) is True
    assert queue.enqueue(job) is False

    stored = queue.dequeue()

    assert stored == job
    assert queue.dequeue() is None


def test_run_lock_allows_one_holder() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis=redis, keys=RedisKeyBuilder("chaincloud"), queue_name="agent_runs")

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
    keys = RedisKeyBuilder("chaincloud")
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
    assert JobPayload.model_validate_json(raw_dead_letter).attempt == 2


def test_dead_letter_jobs_can_be_listed_and_requeued() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    keys = RedisKeyBuilder("chaincloud")
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


def _job(max_attempts: int = 3) -> JobPayload:
    workspace_id = uuid4()
    return JobPayload(
        workspace_id=workspace_id,
        job_type=JobType.AGENT_RUN,
        resource_id=uuid4(),
        idempotency_key=f"agent.run:{workspace_id}:resource",
        max_attempts=max_attempts,
    )
