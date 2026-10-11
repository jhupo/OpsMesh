import asyncio
from time import monotonic
from uuid import uuid4

import fakeredis

from opsmesh.runtime.queues.contracts import JobPayload, JobType
from opsmesh.runtime.queues.notifications import wait_for_notification
from opsmesh.runtime.queues.service import RedisQueue
from opsmesh.shared.redis.keys import RedisKeyBuilder


def test_enqueue_wakes_every_node_without_consuming_or_duplicating_the_job() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    queue = RedisQueue(redis, RedisKeyBuilder("notifications"), "jobs", blocking_timeout_seconds=0)
    job = JobPayload(
        job_type=JobType.CONVERSATION_ADVANCE,
        workspace_id=uuid4(),
        resource_id=uuid4(),
        idempotency_key="one",
    )

    async def exercise():
        with redis.pubsub() as first, redis.pubsub() as second:
            for subscription in (first, second):
                subscription.subscribe(queue.keys.queue(queue.queue_name) + ":ready")
                subscription.get_message()
            waits = [
                asyncio.create_task(wait_for_notification(subscription, 5))
                for subscription in (first, second)
            ]
            await asyncio.sleep(0.05)
            started = monotonic()
            assert queue.enqueue(job)
            await asyncio.wait_for(asyncio.gather(*waits), 1)
            assert monotonic() - started < 1
            assert queue.count_queued() == 1
            assert queue.dequeue().job_id == job.job_id
            assert queue.dequeue() is None

    asyncio.run(exercise())
