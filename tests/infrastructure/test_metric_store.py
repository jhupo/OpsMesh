"""Cumulative process snapshots and an exited Run worker remain visible through HTTP."""

import asyncio
import os
import subprocess
import sys
from time import monotonic
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.entrypoints.http.app import create_http_app
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.infrastructure.telemetry.metric_store import PersistentMetricStore
from opsmesh.infrastructure.telemetry.metrics import PlatformMetrics


async def test_repeated_flush_replaces_cumulative_snapshot_and_closed_gauges_expire(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}")
    first, second = PlatformMetrics(), PlatformMetrics()
    stores = [PersistentMetricStore(database, metrics) for metrics in (first, second)]
    await database.initialize()
    try:
        first.increment("tool_calls")
        first.observe("model_duration_seconds", 0.25)
        first.gauge("active_runs", 10)
        await stores[0].flush()
        await stores[0].flush()
        second.increment("tool_calls")
        second.observe("model_duration_seconds", 0.75)
        second.gauge("active_runs", 4)
        await stores[1].flush()
        # A heartbeat must not make the first worker's older gauge supersede the newer value.
        await stores[0].flush()
        rendered = await stores[1].render()
        assert 'opsmesh_tool_calls_total{outcome="success"} 2' in rendered
        assert "opsmesh_model_duration_seconds_count 2" in rendered
        assert "opsmesh_model_duration_seconds_sum 1\n" in rendered
        assert "opsmesh_active_runs 4\n" in rendered
        await stores[0].close()
        await stores[1].close()
        rendered = await stores[1].render()
        assert "opsmesh_model_duration_seconds_count 2" in rendered
        assert "opsmesh_active_runs 0\n" in rendered
        assert len(await stores[0].records.list()) == 2
    finally:
        await asyncio.gather(*(store.close() for store in stores))
        await database.close()


async def test_heartbeat_flushes_without_http_or_queue_coordinator(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}")
    metrics = PlatformMetrics()
    store = PersistentMetricStore(database, metrics, flush_interval_seconds=0.01)
    await database.initialize()
    try:
        await store.start()
        metrics.observe("run_duration_seconds", 3.5)
        for _ in range(50):
            saved = await store.records.get(store.instance_id)
            if "run_duration_seconds" in saved.data["snapshot"]["histograms"]:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Periodic flush never persisted the observation")
        assert saved.data["active"]
        await store.close()
        saved = await store.records.get(store.instance_id)
        assert not saved.data["active"] and saved.data["expires_at"] == 0
    finally:
        await store.close()
        await database.close()


async def test_expired_worker_gauges_do_not_mask_live_snapshot(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}")
    metrics = PlatformMetrics()
    old = PersistentMetricStore(database, metrics)
    viewer = PersistentMetricStore(database, PlatformMetrics())
    await database.initialize()
    try:
        metrics.gauge("pending_memory_jobs", 100)
        await old.flush()
        record = await old.records.get(old.instance_id)
        await old.records.update(record.id, dict(record.data, expires_at=0), record.revision)
        assert "opsmesh_pending_memory_jobs 0\n" in await viewer.render()
    finally:
        await old.close()
        await viewer.close()
        await database.close()


async def test_unavailable_metric_database_does_not_block_shutdown(tmp_path, monkeypatch):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}")
    store = PersistentMetricStore(database, PlatformMetrics(), io_timeout_seconds=0.02)
    await database.initialize()

    async def unavailable(*args):
        await asyncio.Event().wait()

    monkeypatch.setattr(store.records, "get", unavailable)
    try:
        with pytest.raises(OpsMeshError) as failure:
            await store.render()
        assert failure.value.code == "DEPENDENCY_UNAVAILABLE"
        started = monotonic()
        await store.close()
        assert monotonic() - started < 0.5
    finally:
        await database.close()


async def test_exited_real_run_worker_metrics_are_aggregated_by_http(tmp_path, monkeypatch):
    module = f"metrics_plugin_{uuid4().hex}"
    (tmp_path / f"{module}.py").write_text(
        """
from opsmesh.dependencies import get_runtime_extensions
from opsmesh.framework.agents import AgentBackendCapabilities, AgentRunCompleted
class MetricsBackend:
    id = "metrics_fixture"
    version = "1"
    features = AgentBackendCapabilities()
    async def run(self, spec, input, context, **options):
        context.services.observe_metric("model_duration_seconds", 0.25)
        return AgentRunCompleted(output="worker completed")
get_runtime_extensions().register_backend("metrics_fixture", MetricsBackend)
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    settings = OpsMeshSettings(
        data_directory=tmp_path / "data", worker_enabled=False, plugins=(module,)
    )
    application = OpsMeshApplication(settings)
    process = None
    try:
        await application.initialize(admin_username="admin", admin_password="test-password-123")
        await application.start()
        user = (await application.services.accounts.list_users())[0]
        draft = await application.services.agents.create(
            AgentSpecification(id="metrics", name="Metrics", backend="metrics_fixture"), user.id
        )
        await application.services.agents.publish(draft.id, draft.revision)
        session = await application.services.sessions.open(user.id, "metrics@1")
        run = await application.submit_run(
            principal_id=user.id,
            session_id=session.id,
            agent_ref="metrics@1",
            input="hello",
            idempotency_key="worker",
        )
        environment = dict(
            os.environ, PYTHONPATH=str(tmp_path), OPSMESH_WORKER_SETTINGS=settings.model_dump_json()
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "opsmesh.entrypoints.run_worker",
            "--run-id",
            run.id,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        output, errors = await asyncio.wait_for(process.communicate(), timeout=30)
        assert process.returncode == 0, (output.decode(), errors.decode())
        assert (await application.services.runtime.get_run(run.id)).status == "completed"
        records = await application.services.metric_store.records.list()
        assert any(not item.data["active"] for item in records)
        api = create_http_app(application)
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url="http://opsmesh.test"
        ) as client:
            login = await client.post(
                "/api/v1/auth/login", json={"username": "admin", "password": "test-password-123"}
            )
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['token']}"}
            for _ in range(3):
                response = await client.get("/api/v1/metrics", headers=headers)
                assert response.status_code == 200, response.text
                assert "opsmesh_model_duration_seconds_count 1\n" in response.text
                assert "opsmesh_model_duration_seconds_sum 0.25\n" in response.text
                assert "opsmesh_run_duration_seconds_count 1\n" in response.text
                assert "opsmesh_queue_wait_seconds_count 1\n" in response.text
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        await application.close()
