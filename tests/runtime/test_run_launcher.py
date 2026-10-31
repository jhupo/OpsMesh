"""Isolated worker argument construction and real subprocess supervision."""

import asyncio
import os
import sys
from uuid import uuid4

import pytest

from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.bootstrap.workers import create_run_worker_launcher
from opsmesh.framework.errors import OpsMeshError


class Runtime:
    async def claim_worker_launch(self, *args):
        return True

    async def renew_worker_launch(self, *args):
        return True

    async def cancel(self, run_id, **kwargs):
        pass


def test_docker_worker_enforces_declared_limits_without_host_socket(tmp_path):
    launcher = create_run_worker_launcher(
        Runtime(),
        OpsMeshSettings(data_directory=tmp_path),
        mode="docker",
        docker_image="opsmesh:test",
        cpu=1.5,
        memory_mb=384,
        pids_limit=32,
        network="none",
    )
    argv = launcher.command("run1")
    assert argv[argv.index("--cpus") + 1] == "1.5"
    assert argv[argv.index("--memory") + 1] == "384m"
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv and "--cap-drop" in argv
    assert not any("docker.sock" in item for item in argv)
    # The distributed image defaults to ENTRYPOINT ["opsmesh"]. Override it explicitly.
    assert argv[argv.index("--entrypoint") + 1] == "python"
    assert argv[argv.index("opsmesh:test") + 1 :] == (
        "-m",
        "opsmesh.entrypoints.run_worker",
        "--run-id",
        "run1",
    )
    assert "/var/lib/opsmesh" in launcher._environment()["OPSMESH_WORKER_SETTINGS"]


def test_worker_environment_only_passes_explicit_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("ALLOWED_MODEL_KEY", "allowed")
    monkeypatch.setenv("OTHER_PRIVATE_KEY", "hidden")
    launcher = create_run_worker_launcher(
        Runtime(),
        OpsMeshSettings(data_directory=tmp_path),
        mode="subprocess",
        environment_allowlist=("ALLOWED_MODEL_KEY",),
    )
    environment = launcher._environment()
    assert environment["ALLOWED_MODEL_KEY"] == "allowed"
    assert "OTHER_PRIVATE_KEY" not in environment
    assert '"worker_enabled":false' in environment["OPSMESH_WORKER_SETTINGS"]


@pytest.mark.asyncio
async def test_real_subprocess_is_supervised_and_logs_are_persisted(tmp_path, monkeypatch):
    launcher = create_run_worker_launcher(
        Runtime(), OpsMeshSettings(data_directory=tmp_path), mode="subprocess"
    )
    monkeypatch.setattr(
        launcher, "command", lambda run_id: (sys.executable, "-c", "print('worker ran')")
    )
    await launcher.launch("run1")
    await launcher._processes["run1"].wait()
    status = await launcher.poll("run1")
    assert status.status == "exited" and status.exit_code == 0
    assert "worker ran" in next((tmp_path / "worker-logs").glob("*.log")).read_text()
    await launcher.reap()
    assert await launcher.poll("run1") is None
    assert not launcher._lease_tasks and not launcher._tokens
    await launcher.close()


@pytest.mark.asyncio
async def test_reaping_failed_process_reports_owner_before_forgetting(tmp_path, monkeypatch):
    reports = []

    class TrackedRuntime(Runtime):
        async def worker_exited(self, run_id, exit_code, *, launch_token):
            reports.append((run_id, exit_code, launch_token))

    launcher = create_run_worker_launcher(
        TrackedRuntime(), OpsMeshSettings(data_directory=tmp_path), mode="subprocess"
    )
    monkeypatch.setattr(
        launcher, "command", lambda run_id: (sys.executable, "-c", "raise SystemExit(7)")
    )
    await launcher.launch("failed")
    token = launcher._tokens["failed"]
    await launcher._processes["failed"].wait()
    await launcher.reap()
    await launcher.reap()
    assert reports == [("failed", 7, token)]
    assert not launcher._processes and not launcher._lease_tasks and not launcher._tokens
    await launcher.close()


def test_docker_rejects_shared_host_network_and_external_sqlite(tmp_path):
    with pytest.raises(OpsMeshError):
        create_run_worker_launcher(
            Runtime(),
            OpsMeshSettings(data_directory=tmp_path),
            mode="docker",
            docker_image="opsmesh:test",
            network="host",
        )
    launcher = create_run_worker_launcher(
        Runtime(),
        OpsMeshSettings(
            data_directory=tmp_path, database_url="sqlite+aiosqlite:///C:/external/data.db"
        ),
        mode="docker",
        docker_image="opsmesh:test",
    )
    with pytest.raises(OpsMeshError):
        launcher._environment()["OPSMESH_WORKER_SETTINGS"]


@pytest.mark.asyncio
async def test_single_run_worker_loads_plugins_and_finishes_persisted_run(tmp_path, monkeypatch):
    from opsmesh.bootstrap.application import OpsMeshApplication
    from opsmesh.framework.agents import AgentSpecification

    module = f"worker_plugin_{uuid4().hex}"
    (tmp_path / f"{module}.py").write_text(
        """
import os
from opsmesh.dependencies import get_runtime_extensions
from opsmesh.framework.agents import AgentBackendCapabilities, AgentRunCompleted
class ProcessBackend:
    id = "process_fixture"
    version = "1"
    features = AgentBackendCapabilities()
    async def run(self,spec,input,context,**options):
        return AgentRunCompleted(output={"pid":os.getpid(),"text":input})
get_runtime_extensions().register_backend("process_fixture",ProcessBackend)
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    settings = OpsMeshSettings(
        data_directory=tmp_path / "data", plugins=(module,), worker_enabled=False
    )
    application = OpsMeshApplication(settings)
    launcher = create_run_worker_launcher(
        application.services.runtime,
        settings,
        mode="subprocess",
        environment_allowlist=("PYTHONPATH",),
    )
    try:
        await application.initialize()
        await application.start()
        user = await application.services.accounts.create_user(
            "worker-user", "a-strong-test-password", is_admin=True
        )
        draft = await application.services.agents.create(
            AgentSpecification(id="test", name="Test", backend="process_fixture"), user.id
        )
        published = await application.services.agents.publish(draft.id, draft.revision)
        session = await application.services.sessions.open(user.id, published.ref)
        run = await application.submit_run(
            principal_id=user.id,
            session_id=session.id,
            agent_ref=published.ref,
            input="run in a child",
            idempotency_key="worker",
        )
        await launcher.launch(run.id)
        await asyncio.wait_for(launcher._processes[run.id].wait(), timeout=20)
        completed = await application.services.runtime.get_run(run.id)
        logs = "\n".join(
            path.read_text(errors="replace")
            for path in (settings.data_directory / "worker-logs").glob("*.log")
        )
        assert completed.status == "completed", logs
        assert completed.output["pid"] != os.getpid()
        assert completed.output["text"] == "run in a child"
        assert len(await application.services.sessions.history(session.id)) == 2
    finally:
        await launcher.close()
        await application.close()
