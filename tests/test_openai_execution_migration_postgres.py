"""Retired SDK settings remain downgradeable without a runtime compatibility path."""

import asyncio
import json
import os
from uuid import uuid4

import pytest
from agents.extensions.memory import SQLAlchemySession
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import Session

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.agents.sessions.models import (
    AgentSessionMetadata,
    SDKSessionMessageRecord,
    SDKSessionRecord,
)
from opsmesh.agents.sessions.service import AgentSessionService
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.shared.errors import ConflictError
from opsmesh.teams.management.models import AgentTeam
from opsmesh.workspaces.management.models import Workspace
from tests.fixtures.database import temporary_postgres_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="Real PostgreSQL required"
)


@pytest.mark.parametrize("reinitialize_session", [False, True])
def test_retired_provider_configuration_migrates_and_restores_without_rewriting_history(
    reinitialize_session,
):
    with temporary_postgres_schema() as engine:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "0141_archive_export_uncertainty")
        rule = {"id": "write", "action": "product_tool", "decision": "review"}
        settings = {
            "approvals": {
                "claude_permission_mode": "auto",
                "default": "deny",
                "rules": [
                    {"id": "model", "action": "model_request", "decision": "review"},
                    rule,
                ],
            }
        }
        with Session(engine) as session:
            user = User(email=f"{uuid4()}@test.com", display_name="Migration")
            workspace = Workspace(owner=user, name="Migration", slug=uuid4().hex, settings=settings)
            session.add(workspace)
            session.flush()
            profile = AgentProfile(workspace_id=workspace.id, name="Historical", role="expert")
            session.add(profile)
            session.flush()
            team = AgentTeam(workspace_id=workspace.id, name="Historical team")
            session.add(team)
            session.flush()
            credential = ModelProviderCredential(
                workspace_id=workspace.id,
                created_by_user_id=user.id,
                name="Retired",
                provider="anthropic",
                default_model="historical",
                encrypted_api_key="encrypted",
                api_key_fingerprint="fingerprint",
                encryption_key_id="key",
                is_default=True,
                status="active",
                budget_metadata={},
                model_capabilities=[],
            )
            history = {"authorization_snapshot": {"model_provider": {"provider": "anthropic"}}}
            # Seed the historical schema without fields added by later migrations.
            run_id = uuid4()
            session.execute(
                text(
                    "INSERT INTO agent_runs (id, workspace_id, agent_profile_id, input, status) "
                    "VALUES (:id, :workspace_id, :profile_id, CAST(:input AS jsonb), 'completed')"
                ),
                {
                    "id": run_id,
                    "workspace_id": workspace.id,
                    "profile_id": profile.id,
                    "input": json.dumps(history),
                },
            )
            old = AgentSessionMetadata(
                workspace_id=workspace.id,
                session_key=uuid4().hex,
                scope_type="team_agent",
                scope_id=str(team.id),
                agent_profile_id=profile.id,
                agent_team_id=team.id,
                session_metadata={"sdk_provider": "claude_agent_sdk", "other": "preserved"},
                status="active",
            )
            current = AgentSessionMetadata(
                workspace_id=workspace.id,
                session_key=uuid4().hex,
                scope_type="agent",
                scope_id=str(profile.id),
                agent_profile_id=profile.id,
                session_metadata={"sdk_provider": "openai_agents"},
                status="active",
            )
            session.add_all([credential, old, current])
            session.flush()
            session.add(SDKSessionRecord(session_id=old.session_key))
            session.flush()
            original_message = '{"role":"user","content":"retained historical message"}'
            session.add(
                SDKSessionMessageRecord(session_id=old.session_key, message_data=original_message)
            )
            session.commit()
            ids = workspace.id, credential.id, run_id, old.id, current.id
            team_id, profile_id, old_session_key = team.id, profile.id, old.session_key
        command.upgrade(config, "head")
        with Session(engine) as session:
            workspace_id, credential_id, run_id, old_id, current_id = ids
            assert session.get(Workspace, workspace_id).settings == {
                "approvals": {"default": "deny", "rules": [rule]}
            }
            assert session.get(ModelProviderCredential, credential_id).status == "disabled"
            assert session.get(ModelProviderCredential, credential_id).is_default is False
            assert session.get(AgentSessionMetadata, old_id).status == "retired"
            assert session.get(AgentSessionMetadata, old_id).session_metadata == {
                "other": "preserved"
            }
            assert session.get(AgentSessionMetadata, current_id).status == "active"
            assert session.get(AgentSessionMetadata, current_id).session_metadata == {}
            assert session.scalar(select(AgentRun.input).where(AgentRun.id == run_id)) == history
            with pytest.raises(ConflictError, match="Retired sessions cannot be reactivated"):
                AgentSessionService(session).activate_session(
                    workspace_id=workspace_id, session_id=old_id
                )
            replacement = ModelProviderCredential(
                workspace_id=workspace_id,
                name="New default",
                provider="openai",
                default_model="gpt-4.1",
                encrypted_api_key="unused",
                api_key_fingerprint="test",
                encryption_key_id="test",
                is_default=True,
                status="active",
            )
            session.add(replacement)
            session.commit()
            replacement_id = replacement.id
        with pytest.raises(Exception, match="Unset the new active default credential"):
            command.downgrade(config, "0141_archive_export_uncertainty")
        with Session(engine) as session:
            assert session.get(AgentSessionMetadata, old_id).status == "retired"
            assert session.get(ModelProviderCredential, credential_id).status == "disabled"
            session.get(ModelProviderCredential, replacement_id).is_default = False
            session.commit()
        if reinitialize_session:
            with Session(engine) as session:
                summaries = AgentSessionService(session).reset_team_agent_sessions(
                    workspace_id=workspace_id,
                    team_id=team_id,
                    agent_profile_id=profile_id,
                    reason="Switch to supported OpenAI execution",
                )
                assert len(summaries) == 1
                assert session.get(AgentSessionMetadata, old_id).status == "active"
                schema = session.scalar(text("SELECT current_schema()"))
                session.commit()

            async def write_native_history():
                sdk_engine = create_async_engine(
                    engine.url,
                    connect_args={"options": f"-csearch_path={schema},public"},
                )
                try:
                    native = SQLAlchemySession(
                        old_session_key,
                        engine=sdk_engine,
                        sessions_table="sdk_agent_sessions",
                        messages_table="sdk_agent_messages",
                        create_tables=False,
                    )
                    await native.add_items([{"role": "user", "content": "new OpenAI history"}])
                finally:
                    await sdk_engine.dispose()

            asyncio.run(write_native_history())
            with pytest.raises(Exception, match="Remove reinitialized retired sessions"):
                command.downgrade(config, "0141_archive_export_uncertainty")
            with Session(engine) as session:
                assert session.get(AgentSessionMetadata, old_id).status == "active"
                assert session.get(ModelProviderCredential, credential_id).status == "disabled"
                assert session.get(Workspace, workspace_id).settings == {
                    "approvals": {"default": "deny", "rules": [rule]}
                }
                assert json.loads(
                    session.scalar(
                        text("SELECT message_data FROM sdk_agent_messages WHERE session_id=:key"),
                        {"key": old_session_key},
                    )
                ) == {"role": "user", "content": "new OpenAI history"}
                session.delete(session.get(AgentSessionMetadata, old_id))
                session.commit()
        command.downgrade(config, "0141_archive_export_uncertainty")
        with Session(engine) as session:
            assert session.get(Workspace, workspace_id).settings == settings
            assert session.get(ModelProviderCredential, credential_id).status == "active"
            assert session.get(ModelProviderCredential, credential_id).is_default is True
            if reinitialize_session:
                assert session.get(AgentSessionMetadata, old_id) is None
            else:
                assert session.get(AgentSessionMetadata, old_id).status == "active"
                assert session.get(AgentSessionMetadata, old_id).session_metadata == {
                    "sdk_provider": "claude_agent_sdk",
                    "other": "preserved",
                }
                assert (
                    session.scalar(
                        text("SELECT message_data FROM sdk_agent_messages WHERE session_id=:key"),
                        {"key": old_session_key},
                    )
                    == original_message
                )
            assert session.get(AgentSessionMetadata, current_id).session_metadata == {
                "sdk_provider": "openai_agents",
            }
            assert session.scalar(select(AgentRun.input).where(AgentRun.id == run_id)) == history
            session.execute(
                text("UPDATE agent_runs SET status='running' WHERE id=:id"), {"id": run_id}
            )
            session.commit()
        with pytest.raises(Exception, match="Drain non-OpenAI Agent runs"):
            command.upgrade(config, "head")
