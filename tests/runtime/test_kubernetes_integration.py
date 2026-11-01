"""Explicit real cluster acceptance, using a test namespace, PostgreSQL and shared test PVC.

Set OPSMESH_TEST_KUBERNETES_INTEGRATION=1 and OPSMESH_TEST_KUBERNETES_SETTINGS to a
deployment-owned settings JSON path. Job settings must load opsmesh_kubernetes_probe;
its environment Secret sets PYTHONPATH=/var/lib/opsmesh/acceptance-plugins. No paid model.
"""

import asyncio
import os
import socket
import time
from pathlib import Path
from uuid import uuid4

import pytest

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.bootstrap.workers import create_run_worker_launcher
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.run_workers.kubernetes.client import KubernetesAPI
from opsmesh.modules.runtime.api import TERMINAL_STATES

pytestmark = pytest.mark.skipif(
    os.environ.get("OPSMESH_TEST_KUBERNETES_INTEGRATION") != "1",
    reason="Requires explicit real Kubernetes test environment",
)


async def wait_run(application, run_id, statuses, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await application.launcher.reap()
        await application.services.runtime.recover_stale()
        run = await application.services.runtime.get_run(run_id)
        if run.status in statuses:
            return run
        if run.status in TERMINAL_STATES:
            pytest.fail(
                f"Run reached {run.status}: {run.error}; "
                f"logs={await application.launcher.logs(run_id)}"
            )
        await asyncio.sleep(0.5)
    pytest.fail(f"Run timeout: {await application.services.runtime.get_run(run_id)}")


async def wait_job(launcher, run_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = await launcher.poll(run_id)
        if status is not None and status.status == "exited":
            return status
        await asyncio.sleep(0.5)
    pytest.fail(f"Job did not exit: {await launcher.poll(run_id)}")


async def test_real_jobs_complete_resume_cancel_and_reap_start_failure(monkeypatch):
    settings_file = Path(os.environ["OPSMESH_TEST_KUBERNETES_SETTINGS"])
    settings = OpsMeshSettings.model_validate_json(settings_file.read_text(encoding="utf-8"))
    assert settings.run_worker_mode == "kubernetes" and settings.kubernetes_worker
    assert settings.run_worker_image
    assert "opsmesh_kubernetes_probe" in settings.plugins
    directory = settings.data_directory / "acceptance-plugins"
    directory.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).parent / "fixtures" / "kubernetes_probe.py"
    (directory / "opsmesh_kubernetes_probe.py").write_text(source.read_text("utf-8"), "utf-8")
    monkeypatch.syspath_prepend(str(directory))
    application = OpsMeshApplication(settings.model_copy(update={"worker_enabled": False}))
    runs = []
    try:
        await application.initialize()
        await application.start()
        user = await application.services.accounts.create_user(
            "kube-test-" + uuid4().hex[:12],
            "probe-password-" + uuid4().hex,
            is_admin=True,
        )
        agent = await application.services.agents.create(
            AgentSpecification(
                id="probe-" + uuid4().hex, name="Kubernetes acceptance", backend="kubernetes_probe"
            ),
            user.id,
        )
        published = await application.services.agents.publish(agent.id, agent.revision)

        async def submit(input):
            session = await application.services.sessions.open(user.id, published.ref)
            run = await application.submit_run(
                principal_id=user.id,
                session_id=session.id,
                agent_ref=published.ref,
                input=input,
                idempotency_key=uuid4().hex,
            )
            runs.append(run.id)
            return run

        # Two coordinator instances claim/create the same Job; the worker is a real Pod.
        run = await submit("complete")
        other = create_run_worker_launcher(
            application.services.runtime,
            settings,
            mode="kubernetes",
            docker_image=settings.run_worker_image,
            cpu=settings.run_worker_cpu,
            memory_mb=settings.run_worker_memory_mb,
        )
        try:
            await asyncio.gather(application.launcher.launch(run.id), other.launch(run.id))
            outcome = await wait_run(application, run.id, {"completed"})
            assert outcome.output["input"] == "complete"
            assert outcome.output["host"] != socket.gethostname()
            status = await wait_job(other, run.id)
            assert status.exit_code == 0 and status.job_name
            await other.reap()
            assert "opsmesh-probe" in await other.logs(run.id)
        finally:
            await other.close()

        run = await submit("approval")
        await application.launcher.launch(run.id)
        first = await application.launcher.poll(run.id)
        waiting = await wait_run(application, run.id, {"waiting_approval"})
        await wait_job(application.launcher, run.id)
        await application.launcher.reap()
        decided = await application.services.runtime.decide(
            run.id,
            "probe-approval",
            True,
            waiting.revision,
        )
        await application.services.runtime.resume(run.id, decided.revision)
        await application.launcher.launch(run.id)
        second = await application.launcher.poll(run.id)
        assert first.job_name != second.job_name
        outcome = await wait_run(application, run.id, {"completed"})
        assert outcome.output["resumed"] and outcome.output["approved"]
        await wait_job(application.launcher, run.id)
        await application.launcher.reap()

        run = await submit("wait")
        await application.launcher.launch(run.id)
        await wait_run(application, run.id, {"running"})
        await application.launcher.cancel(run.id)
        await wait_run(application, run.id, {"cancelled"})

        # No outbound registry request: Never + a unique missing local image fails by deadline.
        run = await submit("cannot start")
        config = settings.kubernetes_worker.model_copy(
            update={
                "image_pull_policy": "Never",
                "job_deadline_seconds": 30,
            }
        )
        failed = create_run_worker_launcher(
            application.services.runtime,
            settings.model_copy(update={"kubernetes_worker": config}),
            mode="kubernetes",
            docker_image="opsmesh:missing-" + uuid4().hex,
        )
        try:
            await failed.launch(run.id)
            status = await wait_job(failed, run.id, timeout=90)
            assert status.exit_code != 0 and status.reason == "DeadlineExceeded"
            await failed.reap()
            outcome = await application.services.runtime.get_run(run.id)
            assert outcome.status == "failed" and outcome.error.code == "WORKER_START_FAILED"
            assert await failed.logs(run.id)
        finally:
            await failed.close()

        # Coordinator credentials are namespace-scoped and cannot read Secrets.
        api = KubernetesAPI(settings.kubernetes_worker)
        try:
            with pytest.raises(OpsMeshError) as rejected:
                await api.request("GET", f"/api/v1/namespaces/{config.namespace}/secrets")
            assert rejected.value.code == "KUBERNETES_FORBIDDEN"
        finally:
            await api.close()
    finally:
        for run_id in runs:
            await application.launcher.cancel(run_id)
        await application.launcher.close()
        await application.close()
