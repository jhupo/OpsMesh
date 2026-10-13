"""Native Temporal schedule contracts and PostgreSQL admission evidence."""

import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.orchestration.scheduling.models import WorkspaceScheduledJob
from opsmesh.orchestration.scheduling.service import WorkspaceScheduledJobService
from opsmesh.orchestration.scheduling.specs import build_schedule_spec
from opsmesh.orchestration.scheduling.types import ScheduledJobCreateCommand
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.db.base import Base
from tests.fixtures.database import temporary_postgres_schema
from tests.fixtures.temporal import seed_workspace


def test_schedule_spec_uses_native_temporal_calendars() -> None:
    once = build_schedule_spec("one_shot", {"run_at": "2027-01-02T03:04:05Z"})
    assert once.calendars[0].year[0].start == 2027
    assert once.calendars[0].second[0].start == 5
    assert once.time_zone_name == "UTC"
    hourly = build_schedule_spec("hourly", {"minute": 17})
    assert hourly.calendars[0].hour[0].end == 23
    daily = build_schedule_spec("daily", {"time_of_day": "16:45"})
    assert daily.calendars[0].hour[0].start == 16
    with pytest.raises(ValueError):
        build_schedule_spec("hourly", {"minute": True})


@pytest.mark.skipif(not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required")
def test_schedule_lifecycle_commits_native_control_intents_and_rolls_back_atomically() -> None:
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            user, workspace = seed_workspace(session)
            service = WorkspaceScheduledJobService(session)
            schedule = service.create(
                workspace=workspace,
                user_id=user.id,
                data=ScheduledJobCreateCommand(
                    name="Native daily schedule",
                    schedule_type="daily",
                    schedule_config={"time_of_day": "10:00"},
                    action_type="record_due_action",
                    execution_kind=None,
                    resource_id=None,
                    parameters={},
                    metadata={},
                ),
            )
            assert schedule.status == "active"
            assert schedule.next_run_at is None
            service.pause(workspace_id=workspace.id, scheduled_job_id=schedule.id, user_id=user.id)
            assert schedule.status == "paused"
            service.resume(workspace_id=workspace.id, scheduled_job_id=schedule.id, user_id=user.id)
            assert schedule.status == "active"
            intents = list(
                session.scalars(
                    select(TemporalOutboxEntry).where(
                        TemporalOutboxEntry.workspace_id == workspace.id
                    )
                )
            )
            assert len(intents) == 3
            assert all(row.payload["execution"]["kind"] == "schedule_control" for row in intents)
            assert schedule.revision == 3
            schedule.name = "rollback"
            session.rollback()
            assert session.get(WorkspaceScheduledJob, schedule.id).name == "Native daily schedule"
