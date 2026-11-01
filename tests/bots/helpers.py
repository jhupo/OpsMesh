"""Compose the optional protocol implementation explicitly in platform tests."""

from opsmesh.framework.adapters import BotAdapterRegistry, BotWebhookRequest
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.infrastructure.bots.qq import QQAdapter
from opsmesh.modules.bots.internal.service import BotService


def create_bot_service(
    store, resolve_secret, client, authorize, dispatcher, *, driver=None, **options
):
    driver = driver or OpsMeshDriver()
    adapters = BotAdapterRegistry(driver)
    if QQAdapter.name not in driver.adapters:
        adapters.register(QQAdapter(driver, client, resolve_secret))
    return BotService(
        store,
        resolve_secret,
        authorize,
        dispatcher,
        adapters=adapters,
        **options,
    )


def qq_adapter(service):
    return service.adapters.get(QQAdapter.name)


async def receive_qq_event(service, bot_id, body, *, signature=None):
    request = BotWebhookRequest(body, signature or {})
    result = await service.receive_webhook(bot_id, request)
    assert result.receipt is not None
    return result.receipt
