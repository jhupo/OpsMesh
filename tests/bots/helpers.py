"""Compose the optional protocol implementation explicitly in platform tests."""

from opsmesh.framework.adapters import BotAdapterRegistry, BotWebhookRequest
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.infrastructure.bots.onebot import OneBotAdapter
from opsmesh.modules.bots.internal.service import BotService


def create_bot_service(
    store, resolve_secret, client, authorize, dispatcher, *, driver=None, **options
):
    driver = driver or OpsMeshDriver()
    adapters = BotAdapterRegistry(driver)
    if OneBotAdapter.name not in driver.adapters:
        adapters.register(OneBotAdapter(driver, client, resolve_secret))
    return BotService(
        store,
        resolve_secret,
        authorize,
        dispatcher,
        adapters=adapters,
        **options,
    )


def onebot_adapter(service):
    return service.adapters.get(OneBotAdapter.name)


async def receive_onebot(service, bot_id, body, *, signature=None):
    request = BotWebhookRequest(body, {"X-Signature": signature} if signature is not None else {})
    result = await service.receive_webhook(bot_id, request)
    assert result.receipt is not None
    return result.receipt
