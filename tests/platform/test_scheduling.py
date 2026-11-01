"""Platform schedules work without an installed business plugin."""

from datetime import UTC, datetime
from typing import Annotated

import pytest

from opsmesh.dependencies import get_scheduling
from opsmesh.framework.dependencies import DependencyScope, Depends
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.scheduling.api import PersistentSchedule, SchedulingService

from .test_application import configured_application


async def test_core_schedule_submits_agent_run_with_no_plugins(tmp_path):
    app, user, _ = await configured_application(tmp_path)
    try:
        assert app.settings.plugins == ()
        await app.start()
        due = datetime.now(UTC)

        async def schedule_task(
            scheduling: Annotated[SchedulingService, Depends(get_scheduling)],
        ) -> PersistentSchedule:
            return await scheduling.create(
                PersistentSchedule(
                    id="daily-report",
                    principal_id=user.id,
                    next_run_at=due,
                    agent_revision="helper@1",
                    payload={"task": "report"},
                ),
                actor_id=user.id,
            )

        async with DependencyScope() as scope:
            schedule = await scope.invoke(schedule_task)
        assert schedule.id == "daily-report"
        assert await app.services.scheduler.tick(now=due) == 1
        runs = await app.services.runtime.list_runs(user.id)
        assert len(runs) == 1 and runs[0].status == "queued"
        assert runs[0].published_agent_ref == "helper@1"
        assert await app.services.scheduler.tick(now=due) == 0
        assert len(await app.services.runtime.list_runs(user.id)) == 1
        assert not (await get_scheduling().list_schedules(actor_id=user.id))[0].enabled

        completed = await app.services.runtime.execute(runs[0].id)
        assert completed.status == "completed", completed.error
        assert completed.output == 'processed:{"task": "report"}'
    finally:
        await app.close()


async def test_injected_scheduler_preserves_platform_authorization(tmp_path):
    app, user, _ = await configured_application(tmp_path)
    try:
        outsider = await app.services.accounts.create_user("outsider", "outsider-passphrase")

        async def denied(
            scheduling: Annotated[SchedulingService, Depends(get_scheduling)],
        ) -> PersistentSchedule:
            return await scheduling.create(
                PersistentSchedule(
                    id="unauthorized",
                    principal_id=user.id,
                    next_run_at=datetime.now(UTC),
                ),
                actor_id=outsider.id,
            )

        async with DependencyScope() as scope:
            with pytest.raises(OpsMeshError) as error:
                await scope.invoke(denied)
        assert error.value.code == "FORBIDDEN"
        assert await get_scheduling().list_schedules(actor_id=user.id) == []
    finally:
        await app.close()
