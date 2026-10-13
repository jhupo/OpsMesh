"""Host maintenance rejects foreign pollers using native Temporal queue metadata."""

import asyncio
import os
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.runtime import Runtime, TelemetryConfig
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from opsmesh.runtime.temporal.ownership import require_managed_worker_ownership
from opsmesh.shared.config import Settings

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_TEMPORAL"), reason="Native Temporal server required"
)


@activity.defn(name="acceptance.ownership_probe")
async def _probe() -> None:
    pass


@pytest.mark.parametrize("queue_suffix", ["", "-archives"])
def test_host_maintenance_rejects_unmanaged_worker(queue_suffix: str) -> None:
    async def verify() -> None:
        environment = await WorkflowEnvironment.start_local(
            dev_server_log_level="error"
        )
        settings = Settings(
            environment="test",
            temporal_task_queue=f"ownership-{uuid4().hex}",
            temporal_worker_identity=f"managed-{uuid4().hex}",
        )
        queue = settings.temporal_task_queue + queue_suffix
        try:
            async with Worker(
                environment.client,
                task_queue=queue,
                identity=settings.temporal_worker_identity,
                activities=[_probe],
            ):
                await asyncio.sleep(0.3)
                await require_managed_worker_ownership(environment.client, settings)
                foreign_client = await Client.connect(
                    environment.client.service_client.config.target_host,
                    namespace=environment.client.namespace,
                    runtime=Runtime(telemetry=TelemetryConfig()),
                )
                async with Worker(
                    foreign_client,
                    task_queue=queue,
                    identity=f"foreign-{uuid4().hex}",
                    activities=[_probe],
                ):
                    for _ in range(30):
                        try:
                            await require_managed_worker_ownership(environment.client, settings)
                        except ValueError as exc:
                            assert "Unmanaged Temporal Workers" in str(exc)
                            break
                        await asyncio.sleep(0.1)
                    else:
                        pytest.fail("Native task queue never exposed the foreign Activity poller")
        finally:
            await environment.shutdown()

    asyncio.run(verify())
