"""Production Worker composition registers and polls with the locked native SDKs."""

import asyncio
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, closing
from datetime import timedelta
from uuid import uuid4

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.orm import sessionmaker
from temporalio.contrib.opentelemetry import create_tracer_provider
from temporalio.testing import WorkflowEnvironment

from opsmesh.agents.sessions.activities import SessionActivities
from opsmesh.resources.storage.backends import create_storage
from opsmesh.resources.transfers.worker import build_archive_temporal_worker
from opsmesh.runtime.sandboxes.factory import build_sandbox_backends
from opsmesh.runtime.temporal.client import connect_temporal
from opsmesh.runtime.temporal.converter import sdk_data_converter
from opsmesh.runtime.temporal.database import temporal_database
from opsmesh.runtime.temporal.ownership import require_managed_worker_ownership
from opsmesh.runtime.temporal.worker import build_temporal_worker
from opsmesh.shared.config import Settings
from tests.fixtures.database import product_engine
from tests.fixtures.worker_bootstrap import BootstrapWorkflow, bootstrap_probe

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_TEMPORAL"), reason="Native Temporal server required"
)


def test_production_workers_share_owned_resources_and_poll_native_queues() -> None:
    # OTel owns a process-global provider that its public API cannot reset.
    if os.getenv("OPSMESH_ISOLATED_TRACING_TEST") != "1":
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                f"{__file__}::test_production_workers_share_owned_resources_and_poll_native_queues",
            ],
            env={**os.environ, "OPSMESH_ISOLATED_TRACING_TEST": "1"},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return
    exporter = InMemorySpanExporter()
    provider = create_tracer_provider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    engine = product_engine()
    settings = Settings(
        environment="test",
        database_url=engine.url.render_as_string(hide_password=False),
        temporal_task_queue=f"bootstrap-{uuid4().hex}",
        temporal_worker_identity=f"managed-{uuid4().hex}",
        tracing_enabled=True,
        otel_exporter_otlp_endpoint="http://127.0.0.1:4317",
    )

    async def verify() -> None:
        environment = await WorkflowEnvironment.start_local(
            data_converter=sdk_data_converter,
            dev_server_log_level="error",
        )
        try:
            client = await connect_temporal(
                settings.model_copy(
                    update={
                        "temporal_address": environment.client.service_client.config.target_host
                    }
                )
            )
            async with AsyncExitStack() as resources:
                database = await resources.enter_async_context(temporal_database(settings))
                sessions = SessionActivities(
                    database.engine, sessionmaker(engine, expire_on_commit=False)
                )
                resources.push_async_callback(sessions.close)
                backends = resources.enter_context(build_sandbox_backends(settings))
                storage = resources.enter_context(closing(create_storage(settings)))
                executor = resources.enter_context(
                    ThreadPoolExecutor(max_workers=settings.temporal_blocking_activity_workers)
                )
                archive_executor = resources.enter_context(
                    ThreadPoolExecutor(max_workers=settings.temporal_archive_activity_concurrency)
                )
                worker = build_temporal_worker(
                    client,
                    settings=settings,
                    session_activities=sessions,
                    activity_executor=executor,
                    sandbox_backends=backends,
                    storage=storage,
                    additional_workflows=[BootstrapWorkflow],
                    additional_activities=[bootstrap_probe],
                )
                archive_worker = build_archive_temporal_worker(
                    client, settings=settings, activity_executor=archive_executor
                )
                await resources.enter_async_context(worker)
                await resources.enter_async_context(archive_worker)
                result = await client.execute_workflow(
                    BootstrapWorkflow.run,
                    "native production composition",
                    id=f"bootstrap-{uuid4().hex}",
                    task_queue=settings.temporal_task_queue,
                    execution_timeout=timedelta(seconds=30),
                )
                assert result == "native production composition"
                await require_managed_worker_ownership(client, settings)
        finally:
            await environment.shutdown()

    try:
        asyncio.run(verify())
        names = [span.name for span in exporter.get_finished_spans()]
        assert names.count("RunWorkflow:acceptance.bootstrap") == 1
        assert names.count("RunActivity:acceptance.bootstrap") == 1
    finally:
        provider.shutdown()
