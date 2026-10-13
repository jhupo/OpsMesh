"""Metadata column migration preserves descriptive data and supports rollback."""

import json
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from opsmesh.identity.users.models import User
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.database import temporary_postgres_schema


def test_runtime_space_metadata_upgrade_and_downgrade_preserve_payloads() -> None:
    with temporary_postgres_schema() as engine:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "0143_execution_release_boundary")
        with Session(engine) as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Metadata owner")
            workspace = Workspace(owner=user, name="Metadata workspace", slug=uuid4().hex)
            session.add_all([user, workspace])
            session.commit()
            workspace_id = workspace.id
        identifier = uuid4()
        storage = {"description": "External storage arrangement", "nested": {"key": "kept"}}
        cleanup = {"completed_task_retention_days": 90, "note": "Informational only"}
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO runtime_spaces (id, workspace_id, name, scope, status, policy, "
                    "storage_policy, cleanup_policy) VALUES (:id, :workspace, 'Metadata', "
                    "'workspace', 'active', '{}', CAST(:storage AS jsonb), CAST(:cleanup AS jsonb))"
                ),
                {
                    "id": identifier,
                    "workspace": workspace_id,
                    "storage": json.dumps(storage),
                    "cleanup": json.dumps(cleanup),
                },
            )
        command.upgrade(config, "0144_runtime_space_metadata")
        columns = {column["name"] for column in inspect(engine).get_columns("runtime_spaces")}
        assert {"storage_metadata", "cleanup_metadata"} <= columns
        assert not {"storage_policy", "cleanup_policy"} & columns
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT storage_metadata, cleanup_metadata FROM runtime_spaces WHERE id = :id"
                ),
                {"id": identifier},
            ).one()
            assert tuple(row) == (storage, cleanup)
        command.downgrade(config, "0143_execution_release_boundary")
        columns = {column["name"] for column in inspect(engine).get_columns("runtime_spaces")}
        assert {"storage_policy", "cleanup_policy"} <= columns
        assert not {"storage_metadata", "cleanup_metadata"} & columns
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT storage_policy, cleanup_policy FROM runtime_spaces WHERE id = :id"),
                {"id": identifier},
            ).one()
            assert tuple(row) == (storage, cleanup)
