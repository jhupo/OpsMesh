"""Bot bindings execute the default Agent path without a business plugin."""

import hashlib
import hmac
import json
from typing import Annotated

from opsmesh.dependencies import get_bots
from opsmesh.framework.dependencies import DependencyScope, Depends
from opsmesh.modules.bots.api import BotAccount, BotAccounts
from tests.bots.helpers import receive_onebot

from .test_application import configured_application


async def test_bound_bot_runs_agent_without_plugins_and_unbind_stops_future_runs(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PLATFORM_BOT_SECRET", "offline-test-secret")
    app, user, _ = await configured_application(tmp_path)
    try:
        assert app.settings.plugins == ()
        await app.start()
        await app.services.bots.create_account(
            BotAccount(
                id="support",
                self_id="100",
                adapter="onebot_v11",
                settings={"api_base_url": "http://onebot.test"},
                credentials={"webhook_secret": "env:PLATFORM_BOT_SECRET"},
            ),
            actor_id=user.id,
        )
        await app.services.bots.bind_identity("support", "200", user.id, actor_id=user.id)

        async def bind(bots: Annotated[BotAccounts, Depends(get_bots)]):
            return await bots.bind_route("support", "private:200", "helper", actor_id=user.id)

        async with DependencyScope() as scope:
            binding = await scope.invoke(bind)
        assert binding.agent_revision == "helper@1"

        async def receive(message_id: int):
            body = json.dumps(
                {
                    "time": 1700000000,
                    "post_type": "message",
                    "self_id": 100,
                    "message_type": "private",
                    "message_id": message_id,
                    "user_id": 200,
                    "message": [{"type": "text", "data": {"text": "hello"}}],
                }
            ).encode()
            signature = "sha1=" + hmac.new(b"offline-test-secret", body, hashlib.sha1).hexdigest()
            return await receive_onebot(app.services.bots, "support", body, signature=signature)

        receipt = await receive(1)
        assert receipt.status == "processed" and receipt.matched == 1
        runs = await app.services.runtime.list_runs(user.id)
        assert len(runs) == 1 and runs[0].published_agent_ref == "helper@1"
        assert runs[0].metadata["reply_target"]["channel_id"] == "private:200"
        assert (await receive(1)).status == "duplicate"

        # Unbinding controls later ingress; it does not cancel a previously accepted Run.
        await app.services.bots.unbind_route("support", "private:200", actor_id=user.id)
        assert (await receive(2)).matched == 0
        assert len(await app.services.runtime.list_runs(user.id)) == 1
        completed = await app.services.runtime.execute(runs[0].id)
        assert completed.status == "completed", completed.error
        assert completed.output == "processed:hello"
    finally:
        await app.close()
