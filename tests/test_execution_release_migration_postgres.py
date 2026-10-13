"""Upgrade preserves completed executions without releasing live cancellation work."""

from datetime import UTC, datetime
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from opsmesh.identity.users.models import User
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.database import temporary_postgres_schema


def test_execution_release_upgrade_and_downgrade_preserve_terminal_evidence():
    with temporary_postgres_schema() as engine:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "0142_openai_execution_only")
        with Session(engine) as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Execution owner")
            workspace = Workspace(owner=user, name="Release boundary", slug=uuid4().hex)
            session.add_all([user, workspace])
            session.commit()
            workspace_id = workspace.id
        completed = datetime(2026, 10, 12, 1, 7, 3, tzinfo=UTC)
        identifiers = {
            status: uuid4() for status in ("completed", "failed", "cancelled", "running")
        }
        with engine.begin() as connection:
            for status, identifier in identifiers.items():
                connection.execute(
                    text(
                        "INSERT INTO agent_runs (id, workspace_id, status, input, started_at, "
                        "completed_at) VALUES (:id, :workspace, :status, '{}', :start, :end)"
                    ),
                    {
                        "id": identifier,
                        "workspace": workspace_id,
                        "status": status,
                        "start": completed,
                        "end": None if status == "running" else completed,
                    },
                )
        command.upgrade(config, "head")
        column = next(
            item
            for item in inspect(engine).get_columns("agent_runs")
            if item["name"] == "execution_released_at"
        )
        assert column["type"].timezone
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT status, execution_released_at FROM agent_runs")
            ).all()
            assert dict(rows) == {
                status: None if status == "running" else completed for status in identifiers
            }
        command.downgrade(config, "0142_openai_execution_only")
        assert "execution_released_at" not in {
            column["name"] for column in inspect(engine).get_columns("agent_runs")
        }
        with engine.connect() as connection:
            assert dict(connection.execute(text("SELECT id, status FROM agent_runs")).all()) == {
                identifier: status for status, identifier in identifiers.items()
            }
