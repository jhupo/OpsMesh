"""Verify the actual Alembic migration and native SDK persistence in an isolated schema."""

import asyncio
import json
import os
import selectors
import sys
from uuid import uuid4

import pytest
from agents.extensions.memory import SQLAlchemySession
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from opsmesh.identity.users.models import User
from opsmesh.workspaces.management.models import Workspace

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL integration URL required"
)


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
