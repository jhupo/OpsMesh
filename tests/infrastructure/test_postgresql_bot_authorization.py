"""Bot identity authorization snapshots remain fenced across PostgreSQL instances."""

import asyncio
import os

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.bots.api import BotAccount, BotIdentity, BotRoute
from opsmesh.modules.bots.internal.configuration_store import BotConfigurationStore
from opsmesh.modules.bots.internal.record_keys import bot_record_key
from tests.infrastructure.test_postgresql import postgres as postgres
from tests.infrastructure.test_postgresql import postgres_url as postgres_url

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPSMESH_TEST_POSTGRES_INTEGRATION") != "1",
        reason="Explicit temporary PostgreSQL integration",
    ),
]


@pytest.mark.parametrize("operation", ["configuration", "route"])
async def test_postgres_bot_configuration_and_identity_snapshot_race(postgres_url, operation):
    databases = [AsyncDatabase(postgres_url), AsyncDatabase(postgres_url)]
    records = [VersionedRecordStore(database, "bots_security") for database in databases]
    configurations = [BotConfigurationStore(store) for store in records]
    try:
        await databases[0].initialize()
        await databases[1].verify_schema()
        for index in range(10):
            account = BotAccount(
                id=f"support-{index}",
                self_id=str(100 + index),
                adapter="onebot_v11",
                settings={"api_base_url": "https://trusted.invalid"},
                credentials={"webhook_secret": "env:BOT_SECRET"},
            )
            identifier = bot_record_key("account", account.id)
            await records[0].create(
                identifier, {"kind": "account", "account": account.model_dump(mode="json")}
            )
            identity = BotIdentity(bot_id=account.id, external_user_id="200", principal_id="admin")
            # Each caller authorized revision 1 with no existing identity. Only one
            # request may commit, whichever obtains the account row lock first.
            results = await asyncio.gather(
                configurations[0].replace_account(
                    account.model_copy(
                        update={
                            "settings": {
                                **account.settings,
                                "api_base_url": "https://replacement.invalid",
                            }
                        }
                    ),
                    1,
                    {},
                    [],
                )
                if operation == "configuration"
                else configurations[0].save_route(
                    BotRoute(
                        bot_id=account.id, channel_id="private:200", agent_revision="controlled@1"
                    ),
                    1,
                    None,
                    {},
                    [],
                ),
                configurations[1].save_identity(identity, 1, None, [], {}),
                return_exceptions=True,
            )
            failures = [result for result in results if isinstance(result, BaseException)]
            assert len(failures) == 1
            assert isinstance(failures[0], OpsMeshError) and failures[0].code == "CONFLICT"
            saved = await records[0].get(identifier)
            mapping = await records[0].get(bot_record_key("identity", account.id, "200"))
            if operation == "route":
                route = await records[0].get(bot_record_key("route", account.id, "private:200"))
                assert (route is None) != (mapping is None)
                assert saved.revision == 1
            elif mapping is None:
                assert saved.revision == 2
                assert (
                    saved.data["account"]["settings"]["api_base_url"]
                    == "https://replacement.invalid"
                )
            else:
                assert saved.revision == 1
                assert (
                    saved.data["account"]["settings"]["api_base_url"] == "https://trusted.invalid"
                )
    finally:
        await asyncio.gather(*(database.close() for database in databases))
