"""Retire model fallback policy without mutating frozen execution history."""

import json
import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.database import temporary_postgres_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="Real PostgreSQL required"
)


def test_model_fallback_retirement_preserves_policy_for_downgrade_and_historical_snapshot():
    with temporary_postgres_schema() as engine:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "0136_agent_tool_allowlist")
        policy = {"enabled": True, "candidates": [{"credential_id": str(uuid4()), "model": "x"}]}
        with Session(engine) as session:
            user = User(email=f"{uuid4()}@test.com", display_name="Migration")
            workspace = Workspace(
                owner=user,
                name="Policy retirement",
                slug=uuid4().hex,
                settings={"model_provider_fallback": policy, "other": "preserved"},
            )
            untouched = Workspace(owner=user, name="Untouched", slug=uuid4().hex, settings={})
            session.add_all([workspace, untouched])
            session.flush()
            profile = AgentProfile(workspace_id=workspace.id, name="Agent", role="expert")
            session.add(profile)
            session.flush()
            history = {"authorization_snapshot": {"model_provider_fallback": policy}}
            run_id = uuid4()
            # Seed the historical schema without fields introduced by later migrations.
            session.execute(
                text(
                    "INSERT INTO agent_runs (id, workspace_id, agent_profile_id, status, input) "
                    "VALUES (:id, :workspace, :profile, 'completed', CAST(:input AS jsonb))"
                ),
                {
                    "id": run_id,
                    "workspace": workspace.id,
                    "profile": profile.id,
                    "input": json.dumps(history),
                },
            )
            session.commit()
            workspace_id, untouched_id = workspace.id, untouched.id
        command.upgrade(config, "0137_retired_model_fallback")
        with Session(engine) as session:
            assert session.get(Workspace, workspace_id).settings == {"other": "preserved"}
            assert session.get(Workspace, untouched_id).settings == {}
            assert session.scalar(select(AgentRun.input).where(AgentRun.id == run_id)) == history
            session.get(Workspace, workspace_id).settings = {"other": "updated"}
            session.commit()
            assert session.execute(
                text("SELECT workspace_id, value FROM retired_model_fallback_settings")
            ).one() == (workspace_id, policy)
        command.downgrade(config, "0136_agent_tool_allowlist")
        with Session(engine) as session:
            assert session.get(Workspace, workspace_id).settings == {
                "other": "updated",
                "model_provider_fallback": policy,
            }
            assert session.get(Workspace, untouched_id).settings == {}
            assert session.scalar(select(AgentRun.input).where(AgentRun.id == run_id)) == history
            assert session.scalar(select(AgentRun.id)) == run_id
