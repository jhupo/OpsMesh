"""Database commits wake tenant-scoped subscribers; notifications are never facts."""

import asyncio
import os
from uuid import uuid4

import pytest
from psycopg import AsyncConnection
from sqlalchemy.engine import make_url

from opsmesh.orchestration.tasks.notifications import (
    TASK_EVENT_NOTIFICATION_CHANNEL,
    TaskEventNotificationListener,
    TaskEventNotifications,
)


def test_notification_fans_out_and_removes_disconnected_subscribers() -> None:
    async def verify():
        notifications = TaskEventNotifications()
        workspace_id, task_id = uuid4(), uuid4()
        with (
            notifications.subscribe(workspace_id, task_id) as first,
            notifications.subscribe(workspace_id, task_id) as second,
            notifications.subscribe(uuid4(), task_id) as other,
        ):
            notifications.wake(workspace_id, task_id)
            assert first.is_set() and second.is_set()
            assert not other.is_set()
        assert not notifications._subscribers

    asyncio.run(verify())


@pytest.mark.skipif(not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required")
def test_postgres_notification_waits_for_commit_and_rollback_stays_quiet() -> None:
    async def verify():
        url = os.environ["OPSMESH_TEST_POSTGRES_URL"]
        dsn = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
        notifications = TaskEventNotifications()
        listener = TaskEventNotificationListener(url, notifications)
        stop = asyncio.Event()
        running = asyncio.create_task(listener.run(stop))
        workspace_id, task_id = uuid4(), uuid4()
        try:
            await asyncio.wait_for(listener.ready.wait(), timeout=10)
            with (
                notifications.subscribe(workspace_id, task_id) as first,
                notifications.subscribe(workspace_id, task_id) as second,
            ):
                async with await AsyncConnection.connect(dsn) as connection:
                    await connection.execute(
                        "SELECT pg_notify(%s, %s)",
                        (TASK_EVENT_NOTIFICATION_CHANNEL, f"{workspace_id}:{task_id}"),
                    )
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(first.wait(), timeout=0.05)
                    await connection.rollback()
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(first.wait(), timeout=0.05)
                    await connection.execute(
                        "SELECT pg_notify(%s, %s)",
                        (TASK_EVENT_NOTIFICATION_CHANNEL, f"{workspace_id}:{task_id}"),
                    )
                    await connection.commit()
                    await asyncio.wait_for(asyncio.gather(first.wait(), second.wait()), timeout=2)
        finally:
            stop.set()
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    asyncio.run(verify())
