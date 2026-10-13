"""Verify the actual Alembic migration and native SDK persistence in an isolated schema."""

import asyncio
import json
import os
import selectors
import sys
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from agents.extensions.memory import SQLAlchemySession
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from opsmesh.identity.users.models import User
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.database import temporary_postgres_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL integration URL required"
)


def test_archive_migration_preserves_completed_facts_and_blocks_unknown_downgrade():
    with temporary_postgres_schema() as engine:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "0140_native_plugin_downloads")
        with Session(engine) as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Archive migration")
            workspace = Workspace(owner=user, name="Archive", slug=uuid4().hex)
            session.add_all([user, workspace])
            session.commit()
            user_id, workspace_id = user.id, workspace.id
        jobs = Table("workspace_export_jobs", MetaData(), autoload_with=engine)
        identifiers = {status: uuid4() for status in ("queued", "running", "completed")}
        with engine.begin() as connection:
            for status, identifier in identifiers.items():
                connection.execute(
                    jobs.insert().values(
                        id=identifier,
                        workspace_id=workspace_id,
                        created_by_user_id=user_id,
                        export_type="workspace_archive",
                        status=status,
                        request={},
                        metadata={"preserved": 42},
                        verification_status="unverified",
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            rows = {
                str(row.id): row.status
                for row in connection.execute(text("SELECT id,status FROM workspace_export_jobs"))
            }
            assert rows[str(identifiers["completed"])] == "completed"
            assert (
                rows[str(identifiers["running"])] == rows[str(identifiers["queued"])] == "unknown"
            )
            assert connection.scalar(
                text("SELECT metadata FROM workspace_export_jobs WHERE status='completed'")
            ) == {"preserved": 42}
        with pytest.raises(RuntimeError, match="Settle pending or uncertain archive exports"):
            command.downgrade(config, "0140_native_plugin_downloads")
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE workspace_export_jobs SET status='reconciled' WHERE status='unknown'")
            )
        for active_status in ("queued", "running"):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE workspace_export_jobs SET status=:status WHERE id=:id"),
                    {"status": active_status, "id": identifiers["queued"]},
                )
            with pytest.raises(RuntimeError, match="Settle pending or uncertain archive exports"):
                command.downgrade(config, "0140_native_plugin_downloads")
            assert "execution_identity" in {
                column["name"] for column in inspect(engine).get_columns("workspace_export_jobs")
            }
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE workspace_export_jobs SET status='reconciled' WHERE id=:id"),
                {"id": identifiers["queued"]},
            )
        command.downgrade(config, "0140_native_plugin_downloads")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM workspace_export_jobs WHERE status='completed'")
                )
                == 1
            )


def test_native_migration_preserves_platform_history_and_retires_only_pending_intents():
    with temporary_postgres_schema() as engine:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "0123_mcp_call_diagnostics")
        with Session(engine) as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Platform history")
            workspace = Workspace(owner=user, name="History", slug=uuid4().hex)
            session.add_all([user, workspace])
            session.commit()
            actor_id, workspace_id = user.id, workspace.id
        completed, pending, metric = uuid4(), uuid4(), uuid4()
        with engine.begin() as connection:
            for identifier, status in ((completed, "succeeded"), (pending, "pending")):
                connection.execute(
                    text("""
                    INSERT INTO admin_operation_requests
                    (id, workspace_id, actor_user_id, operation, status, parameters,
                     result, created_at, updated_at)
                    VALUES (:id, :workspace, :actor, 'recover_stale_runs', :status,
                            '{}', '{"observed": 42}', now(), now())
                """),
                    {
                        "id": identifier,
                        "workspace": workspace_id,
                        "actor": actor_id,
                        "status": status,
                    },
                )
            connection.execute(
                text("""
                INSERT INTO platform_metric_snapshots (id, sample_slot, created_at, "values")
                VALUES (:id, 42, now(), '{"requests": 123}')
            """),
                {"id": metric},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(
                text("""
                SELECT status, result FROM admin_operation_requests WHERE id=:id
            """),
                {"id": completed},
            ).one() == ("succeeded", {"observed": 42})
            assert connection.execute(
                text("""
                SELECT status, error_code FROM admin_operation_requests WHERE id=:id
            """),
                {"id": pending},
            ).one() == ("failed", "native_engine_migration_requires_new_request")
            assert connection.scalar(
                text("""
                SELECT "values" FROM platform_metric_snapshots WHERE id=:id
            """),
                {"id": metric},
            ) == {"requests": 123}
        command.downgrade(config, "0123_mcp_call_diagnostics")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM admin_operation_requests")) == 2
            assert connection.scalar(
                text('SELECT "values" FROM platform_metric_snapshots WHERE id=:id'), {"id": metric}
            ) == {"requests": 123}


def test_sdk_session_migration_preserves_history_and_round_trips():
    root_url = make_url(os.environ["OPSMESH_TEST_POSTGRES_URL"])
    schema = f"sdk_test_{uuid4().hex}"
    admin = create_engine(root_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = root_url.update_query_dict({"options": f"-csearch_path={schema},public"})
    engine = create_engine(url)
    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%")
    )
    try:
        command.upgrade(config, "0113_database_configuration")
        with Session(engine) as session:
            user = User(email=f"{uuid4()}@example.com", display_name="Migration owner")
            workspace = Workspace(owner=user, name="Migration", slug=uuid4().hex)
            session.add_all([user, workspace])
            session.commit()
            workspace_id = workspace.id
        session_id = uuid4()
        key = f"{workspace_id}:migration"
        history = [
            {"role": "user", "content": "inspect"},
            {"type": "function_call", "name": "inspect", "call_id": "original", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "original", "output": "evidence"},
        ]
        with engine.begin() as connection:
            connection.execute(
                text("""
                INSERT INTO persistent_agent_sessions
                    (id, workspace_id, session_key, scope_type, scope_id,
                     metadata, status, created_at, updated_at)
                VALUES (:id, :workspace, :key, 'task_agent', 'migration',
                        '{}', 'active', now(), now())
            """),
                {"id": session_id, "workspace": workspace_id, "key": key},
            )
            for sequence, item in enumerate(history, 1):
                connection.execute(
                    text("""
                    INSERT INTO persistent_agent_session_items
                        (id, workspace_id, persistent_session_id, sequence, item, created_at)
                    VALUES (:id, :workspace, :session, :sequence, CAST(:item AS jsonb), now())
                """),
                    {
                        "id": uuid4(),
                        "workspace": workspace_id,
                        "session": session_id,
                        "sequence": sequence,
                        "item": json.dumps(item),
                    },
                )
        command.upgrade(config, "head")
        assert "persistent_agent_session_items" not in inspect(engine).get_table_names(
            schema=schema
        )

        async def native_history():
            async_engine = create_async_engine(url)
            storage = SQLAlchemySession(
                key,
                engine=async_engine,
                create_tables=False,
                sessions_table="sdk_agent_sessions",
                messages_table="sdk_agent_messages",
            )
            try:
                assert await storage.get_items() == history
                await storage.add_items([{"role": "assistant", "content": "confirmed"}])
                assert await storage.pop_item() == {"role": "assistant", "content": "confirmed"}
                assert await storage.get_items() == history
            finally:
                await async_engine.dispose()

        with asyncio.Runner(
            loop_factory=(lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
            if sys.platform == "win32"
            else None,
        ) as runner:
            runner.run(native_history())
        command.downgrade(config, "0113_database_configuration")
        with engine.connect() as connection:
            restored = (
                connection.execute(
                    text("""
                SELECT item FROM persistent_agent_session_items
                WHERE persistent_session_id = :session ORDER BY sequence
            """),
                    {"session": session_id},
                )
                .scalars()
                .all()
            )
            assert restored == history
        command.upgrade(config, "head")
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
