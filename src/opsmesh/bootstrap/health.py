"""Keep one lazy native Temporal client for the API process readiness probe."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

from temporalio.client import Client
from temporalio.service import RPCError

from opsmesh.config import Settings


def temporal_health_probe(settings: Settings) -> Callable[[], Awaitable[bool]]:
    client: Client | None = None

    async def probe() -> bool:
        nonlocal client
        try:
            async with asyncio.timeout(3):
                if client is None:
                    client = await Client.connect(
                        settings.temporal_address,
                        namespace=settings.temporal_namespace,
                        tls=settings.temporal_tls_enabled,
                        api_key=settings.temporal_api_key,
                        lazy=True,
                    )
                return await client.service_client.check_health(timeout=timedelta(seconds=2))
        except (RPCError, RuntimeError, TimeoutError, OSError):
            return False

    return probe
