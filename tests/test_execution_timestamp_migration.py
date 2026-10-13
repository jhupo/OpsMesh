"""Execution ordering survives native persistence and a non-UTC schema upgrade."""

from datetime import UTC, datetime
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import DateTime, select, text
from sqlalchemy.orm import Session

from opsmesh.bootstrap.models import register_models
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun, RunEvent
from opsmesh.shared.db.base import Base
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.database import temporary_postgres_schema


def test_product_timestamps_are_aware_without_changing_native_sdk_schema() -> None:
    register_models()
    native_tables = {"sdk_agent_sessions", "sdk_agent_messages"}
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, DateTime):
                assert column.type.timezone == (table.name not in native_tables), (
                    table.name,
                    column.name,
                )


def test_execution_timestamp_migration_preserves_instants_and_event_order() -> None:
    with temporary_postgres_schema() as engine:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "0137_retired_model_fallback")
        with Session(engine) as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Execution owner")
            workspace = Workspace(owner=user, name="Timestamps", slug=uuid4().hex)
            session.add_all([user, workspace])
            session.commit()
            workspace_id = workspace.id
        run_id, event_id = uuid4(), uuid4()
        started = datetime(2026, 9, 10, 1, 2, 3, tzinfo=UTC)
        completed = datetime(2026, 9, 10, 1, 7, 3, tzinfo=UTC)
        with engine.begin() as connection:
            assert connection.scalar(text("SHOW TimeZone")) != "UTC"
            connection.execute(
                text(
                    "INSERT INTO agent_runs (id, workspace_id, status, input, started_at, "
                    "completed_at) VALUES (:id, :workspace, 'completed', '{}', :start, :end)"
                ),
                {"id": run_id, "workspace": workspace_id, "start": started, "end": completed},
            )
            connection.execute(
                text(
                    "INSERT INTO run_events (id, workspace_id, agent_run_id, event_type, "
                    "sequence, message, metadata, created_at) VALUES "
                    "(:id, :workspace, :run, 'completed', 1, '', '{}', :end)"
                ),
                {"id": event_id, "workspace": workspace_id, "run": run_id, "end": completed},
            )
            before = connection.execute(
                text("SELECT started_at, completed_at FROM agent_runs WHERE id = :id"),
                {"id": run_id},
            ).one()
            assert before.started_at.tzinfo is None
        command.upgrade(config, "0138_execution_timestamps")
        with Session(engine) as session:
            run = session.execute(
                select(AgentRun.started_at, AgentRun.completed_at).where(AgentRun.id == run_id)
            ).one()
            event = session.get(RunEvent, event_id)
            assert run is not None and event is not None
            assert run.started_at == started and run.completed_at == completed
            assert event.created_at == completed and event.created_at >= run.started_at
            # An event and the ordinary product timestamps now share one time domain.
            assert session.scalar(select(AgentRun.id).order_by(AgentRun.started_at)) == run_id
        command.downgrade(config, "0137_retired_model_fallback")
        with engine.connect() as connection:
            after = connection.execute(
                text("SELECT started_at, completed_at FROM agent_runs WHERE id = :id"),
                {"id": run_id},
            ).one()
            assert after == before
