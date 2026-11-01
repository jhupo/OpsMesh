"""Ownership, immutable publication and local account security boundaries."""

import asyncio

import pytest

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant
from opsmesh.modules.accounts.internal.service import LocalAccountService
from opsmesh.modules.agents.internal.catalog import PersistentAgentDefinitionCatalog
from opsmesh.modules.models.api import ModelProfile
from opsmesh.modules.models.internal.service import ModelProfileCatalog
from opsmesh.modules.sessions.internal.service import ConversationService


@pytest.mark.asyncio
async def test_local_tokens_revoke_and_department_grants_are_rechecked(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    accounts = LocalAccountService(database)
    await database.initialize()
    try:
        department = await accounts.create_department("Operations")
        await accounts.create_role(
            AccessRole(
                name="operators",
                grants=(
                    PermissionGrant(action="agent:run", resource="ops@*", department=department.id),
                ),
            )
        )
        user = await accounts.create_user(
            "alice", "strong-passphrase", roles=("operators",), departments=(department.id,)
        )
        token = await accounts.login("ALICE", "strong-passphrase")
        assert (await accounts.authenticate(token.token)).id == user.id
        await accounts.authorize(user, "agent:run", "ops@1")
        with pytest.raises(OpsMeshError, match="not permitted"):
            await accounts.authorize(user, "agent:run", "finance@1")
        await accounts.update_user(user.id, departments=())
        with pytest.raises(OpsMeshError, match="not permitted"):
            await accounts.authorize(user, "agent:run", "ops@1")
        await accounts.update_user(user.id, active=False)
        with pytest.raises(OpsMeshError, match="disabled"):
            await accounts.authenticate(token.token)
        await accounts.update_user(user.id, active=True)
        await accounts.logout(token.token)
        with pytest.raises(OpsMeshError, match="expired"):
            await accounts.authenticate(token.token)
        row = await accounts.users.get(user.id)
        assert row is not None and "strong-passphrase" not in str(row.data)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_session_claims_are_exclusive_and_message_ids_detect_mismatched_replay(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}")
    sessions = ConversationService(database)
    await database.initialize()
    try:
        session = await sessions.get_or_open("bot:group:7", "alice", "agent@1")
        assert (await sessions.get_or_open("bot:group:7", "alice", "agent@1")).id == session.id
        assert (await sessions.get_or_open("bot:group:7", "bob", "agent@1")).id != session.id
        claims = await asyncio.gather(
            sessions.claim(session.id, "one"),
            sessions.claim(session.id, "two"),
            return_exceptions=True,
        )
        assert sum(result is None for result in claims) == 1
        claimed = await sessions.get(session.id)
        await sessions.release(session.id, "wrong-run")
        assert (await sessions.get(session.id)).active_run_id == claimed.active_run_id
        await sessions.append(session.id, "user", "hello", message_id="input")
        await sessions.append(session.id, "user", "hello", message_id="input")
        with pytest.raises(OpsMeshError, match="different content"):
            await sessions.append(session.id, "user", "changed", message_id="input")
        assert len(await sessions.history(session.id)) == 1
        await sessions.release(session.id, claimed.active_run_id)
        await sessions.close(session.id)
        with pytest.raises(OpsMeshError) as error:
            await sessions.claim(session.id, "next")
        assert error.value.code == "SESSION_CLOSED"
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_publication_and_model_profiles_preserve_prior_revisions(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}")
    models = ModelProfileCatalog(database)

    async def validate(specification):
        await models.get(specification.model_profile_ref)

    agents = PersistentAgentDefinitionCatalog(database, validate)
    await database.initialize()
    try:
        await models.create(ModelProfile(id="local", model="test-model"))
        await models.update(ModelProfile(id="local", model="new-model"), 1)
        assert (await models.get("local@1")).model == "test-model"
        assert (await models.get("local@2")).model == "new-model"
        specification = AgentSpecification(id="helper", name="Helper", model_profile_ref="local@1")
        draft = await agents.create(specification, "alice")
        first = await agents.publish(draft.id, draft.revision)
        current = await agents.get(draft.id)
        changed = await agents.update(
            draft.id, specification.model_copy(update={"instructions": "v2"}), current.revision
        )
        second = await agents.publish(draft.id, changed.revision)
        assert first.ref == "helper@1" and second.ref == "helper@2"
        assert (await agents.get_revision(first.ref)).instructions == ""
        with pytest.raises(OpsMeshError) as error:
            await agents.update(draft.id, specification, draft.revision)
        assert error.value.code == "CONFLICT"
    finally:
        await database.close()
