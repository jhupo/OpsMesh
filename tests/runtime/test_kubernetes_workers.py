"""Kubernetes REST lifecycle against durable runtime state, without a paid model or cluster."""

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest

from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.bootstrap.workers import create_run_worker_launcher
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.run_workers.kubernetes.client import KubernetesAPI
from opsmesh.infrastructure.run_workers.kubernetes.configuration import (
    KubernetesWorkerConfiguration,
)
from opsmesh.infrastructure.run_workers.kubernetes.job_spec import build_job, identity
from tests.runtime.test_runtime import ControlledBackend, create_runtime, request


class KubernetesServer:
    def __init__(self):
        self.jobs = {}
        self.pods = {}
        self.deleted = []
        self.creates = 0
        self.lose_create_response = False
        self.requests = []

    def response(self, request):
        self.requests.append(request)
        path = request.url.path
        if "/pods/" in path and path.endswith("/log"):
            return httpx.Response(200, text="worker output\n")
        if path.endswith("/pods"):
            uid = request.url.params["labelSelector"].split("=", 1)[1]
            return httpx.Response(200, json={"items": self.pods.get(uid, [])})
        name = path.rsplit("/", 1)[1]
        if request.method == "POST":
            self.creates += 1
            job = json.loads(request.content)
            name = job["metadata"]["name"]
            if name in self.jobs:
                return httpx.Response(409, json={"reason": "AlreadyExists"})
            job["metadata"]["uid"] = uuid4().hex
            self.jobs[name] = job
            if self.lose_create_response:
                self.lose_create_response = False
                raise httpx.ReadTimeout("Response lost after creation", request=request)
            return httpx.Response(201, json=copy.deepcopy(job))
        if request.method == "DELETE":
            if name not in self.jobs:
                return httpx.Response(404)
            expected = json.loads(request.content)["preconditions"]["uid"]
            if self.jobs[name]["metadata"]["uid"] != expected:
                return httpx.Response(409, json={"reason": "Conflict"})
            self.deleted.append((name, expected))
            del self.jobs[name]
            return httpx.Response(200, json={"status": "Success"})
        if name == "jobs":
            matches = request.url.params["labelSelector"].split(",")
            return httpx.Response(
                200,
                json={
                    "items": [
                        copy.deepcopy(job)
                        for job in self.jobs.values()
                        if all(
                            job["metadata"]["labels"].get(key) == value
                            for key, value in (item.split("=", 1) for item in matches)
                        )
                    ]
                },
            )
        return (
            httpx.Response(200, json=copy.deepcopy(self.jobs[name]))
            if name in self.jobs
            else httpx.Response(404)
        )

    def finish(self, name, code=0):
        job = self.jobs[name]
        job["status"] = {
            "conditions": [
                {
                    "type": "Complete" if code == 0 else "Failed",
                    "status": "True",
                    "reason": "Completed" if code == 0 else "DeadlineExceeded",
                }
            ]
        }
        uid = job["metadata"]["uid"]
        self.pods[uid] = [
            {
                "metadata": {
                    "name": name + "-pod",
                    "ownerReferences": [{"kind": "Job", "uid": uid}],
                },
                "status": {
                    "containerStatuses": [
                        {"name": "run", "state": {"terminated": {"exitCode": code}}}
                    ]
                },
            }
        ]


@asynccontextmanager
async def cluster(tmp_path, backend=None):
    token = tmp_path / "token"
    token.write_text("first-token")
    configuration = KubernetesWorkerConfiguration(
        api_url="https://cluster.test",
        token_file=token,
        ca_file=None,
        environment_secrets=("model-keys",),
    )
    settings = OpsMeshSettings(
        data_directory=tmp_path / "content",
        database_url="postgresql+asyncpg://localhost/test",
        kubernetes_worker=configuration,
        run_worker_mode="kubernetes",
        run_worker_image="opsmesh:test",
    )
    runtime, database = await create_runtime(tmp_path, backend or ControlledBackend())
    server = KubernetesServer()
    async with httpx.AsyncClient(
        base_url=configuration.api_url, transport=httpx.MockTransport(server.response)
    ) as client:

        def launcher():
            return create_run_worker_launcher(
                runtime,
                settings,
                mode="kubernetes",
                docker_image=settings.run_worker_image,
                kubernetes_api=KubernetesAPI(configuration, client),
            )

        try:
            yield launcher, runtime, server, configuration
        finally:
            await runtime.close()
            await database.close()


async def test_concurrent_coordinators_create_one_logical_job_and_rotate_token(tmp_path):
    async with cluster(tmp_path) as (factory, runtime, server, config):
        first, second = factory(), factory()
        run = await runtime.submit(request())
        await asyncio.gather(first.launch(run.id), second.launch(run.id))
        assert len(server.jobs) == 1
        job = next(iter(server.jobs.values()))
        name, token = identity(config, run.id, 0)
        assert job["metadata"]["name"] == name
        assert (await runtime.runs.get(run.id)).data["worker_launch_token"] == token
        config.token_file.write_text("rotated-token")
        assert (await second.poll(run.id)).status == "running"
        assert server.requests[-1].headers["Authorization"] == "Bearer rotated-token"
        await first.close()
        assert server.jobs  # Coordinator shutdown does not stop an independently running Job.


async def test_lost_create_response_is_reconciled_before_retry(tmp_path):
    async with cluster(tmp_path) as (factory, runtime, server, _):
        run = await runtime.submit(request())
        server.lose_create_response = True
        await factory().launch(run.id)
        assert len(server.jobs) == 1 and server.creates == 1
        assert (await runtime.get_run(run.id)).status == "queued"


async def test_restart_renews_pending_job_then_cancel_removes_owned_uid(tmp_path):
    async with cluster(tmp_path) as (factory, runtime, server, _):
        run = await runtime.submit(request())
        await factory().launch(run.id)
        record = await runtime.runs.get(run.id)
        await runtime.runs.update(
            run.id, dict(record.data, worker_launch_expires=0), record.revision
        )
        restarted = factory()
        await restarted.reap()
        renewed = await runtime.runs.get(run.id)
        assert renewed.data["worker_launch_expires"] > 0
        job = next(iter(server.jobs.values()))
        await runtime.cancel(run.id)  # HTTP cancellation is observed without process-local state.
        await restarted.reap()
        assert (await runtime.get_run(run.id)).status == "cancelled"
        assert not server.jobs
        assert server.deleted == [(job["metadata"]["name"], job["metadata"]["uid"])]
        assert (await restarted.poll(run.id)).reason == "Cancelled"


@pytest.mark.parametrize("code", [0, 7])
async def test_job_exit_before_acquisition_fails_run_and_preserves_logs(tmp_path, code):
    async with cluster(tmp_path) as (factory, runtime, server, _):
        run = await runtime.submit(request())
        launcher = factory()
        await launcher.launch(run.id)
        server.finish(next(iter(server.jobs)), code)
        await launcher.reap()
        await launcher.reap()
        failed = await runtime.get_run(run.id)
        assert failed.status == "failed" and failed.error.code == "WORKER_START_FAILED"
        assert not server.jobs and len(server.deleted) == 1
        assert "worker output" in await factory().logs(run.id)
        assert (await factory().poll(run.id)).exit_code == (code or 1)
        await launcher.launch(run.id)
        assert not server.jobs


async def test_approval_finishes_job_and_resume_uses_next_generation(tmp_path):
    backend = ControlledBackend(pause=True)
    async with cluster(tmp_path, backend) as (factory, runtime, server, _):
        launcher = factory()
        run = await runtime.submit(request())
        await launcher.launch(run.id)
        first_name = next(iter(server.jobs))
        first_token = (await launcher.poll(run.id)).launch_token
        waiting = await runtime.execute(run.id, launch_token=first_token)
        assert waiting.status == "waiting_approval"
        server.finish(first_name)
        await launcher.reap()
        assert not server.jobs
        decided = await runtime.decide(run.id, "approve1", True, waiting.revision)
        await runtime.resume(run.id, decided.revision)
        await launcher.launch(run.id)
        second_name = next(iter(server.jobs))
        assert second_name != first_name
        second_token = (await launcher.poll(run.id)).launch_token
        assert second_token != first_token
        completed = await runtime.execute(run.id, launch_token=second_token)
        server.finish(second_name)
        await factory().reap()
        assert completed.status == "completed" and backend.calls == 2
        assert (await factory().poll(run.id)).exit_code == 0


async def test_mutated_ownership_is_never_cancelled_and_uid_replacement_is_fenced(tmp_path):
    async with cluster(tmp_path) as (factory, runtime, server, _):
        run = await runtime.submit(request())
        launcher = factory()
        await launcher.launch(run.id)
        job = next(iter(server.jobs.values()))
        api = launcher._kubernetes.api
        original = copy.deepcopy(job)
        job["metadata"]["uid"] = "replacement"
        with pytest.raises(OpsMeshError, match="UID changed"):
            await api.delete_job(job["metadata"]["name"], original["metadata"]["uid"])
        job["metadata"]["annotations"]["opsmesh.io/launch-token"] = "foreign"
        await launcher.cancel(run.id)
        assert server.jobs and not server.deleted
        # Even the Pod list must match Job owner references, not only a mutable label selector.
        server.pods["replacement"] = [
            {
                "metadata": {
                    "name": "foreign",
                    "ownerReferences": [{"kind": "Job", "uid": "some-other-owner"}],
                }
            }
        ]
        assert await api.pods("replacement") == []


async def test_different_deployment_job_configuration_is_not_overwritten(tmp_path):
    async with cluster(tmp_path) as (factory, runtime, server, _):
        run = await runtime.submit(request())
        await factory().launch(run.id)
        other = factory()
        other._kubernetes.image = "opsmesh:changed"
        with pytest.raises(OpsMeshError, match="different deployment configuration"):
            await other.launch(run.id)
        assert len(server.jobs) == 1 and not server.deleted
        assert (await runtime.get_run(run.id)).status == "queued"


async def test_log_outage_does_not_block_other_jobs_lease_renewal(tmp_path):
    async with cluster(tmp_path) as (factory, runtime, server, _):
        first = await runtime.submit(request())
        second = await runtime.submit(request("second", "another-session"))
        launcher = factory()
        await launcher.launch(first.id)
        first_name = next(iter(server.jobs))
        await launcher.launch(second.id)
        server.finish(first_name, 1)
        record = await runtime.runs.get(second.id)
        await runtime.runs.update(
            second.id,
            dict(record.data, worker_launch_expires=0),
            record.revision,
        )
        api = launcher._kubernetes.api
        original = api.logs

        async def unavailable(name):
            raise OpsMeshError("KUBERNETES_UNAVAILABLE", "Temporary log outage", retryable=True)

        api.logs = unavailable
        await launcher.reap()
        assert (await runtime.runs.get(second.id)).data["worker_launch_expires"] > 0
        assert first_name in server.jobs
        api.logs = original
        await launcher.reap()
        assert first_name not in server.jobs


def test_job_resources_and_secrets_are_declared_without_controller_credentials():
    config = KubernetesWorkerConfiguration(environment_secrets=("model-key",))
    job = build_job(
        config,
        run_id="untrusted/run-id",
        generation=3,
        image="opsmesh@sha256:example",
        cpu=1.5,
        memory_mb=384,
    )
    pod = job["spec"]["template"]["spec"]
    worker = pod["containers"][0]
    assert worker["command"] == ["python", "-m", "opsmesh.entrypoints.run_worker"]
    assert worker["args"] == ["--run-id", "untrusted/run-id"]
    assert job["spec"]["backoffLimit"] == 0 and pod["restartPolicy"] == "Never"
    assert pod["automountServiceAccountToken"] is False
    assert worker["resources"]["limits"]["cpu"] == "1.5"
    assert worker["resources"]["limits"]["memory"] == "384Mi"
    assert worker["securityContext"]["readOnlyRootFilesystem"]
    assert worker["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert worker["env"][0]["valueFrom"]["secretKeyRef"]["name"] == "opsmesh-run-settings"
    assert "hostPath" not in json.dumps(job) and "postgresql" not in json.dumps(job)


def test_kubernetes_requires_postgres_and_https_without_embedded_credentials(tmp_path):
    with pytest.raises(OpsMeshError, match="PostgreSQL"):
        create_run_worker_launcher(
            None,
            OpsMeshSettings(
                data_directory=tmp_path, kubernetes_worker=KubernetesWorkerConfiguration()
            ),
            mode="kubernetes",
            docker_image="x",
        )
    with pytest.raises(ValueError, match="HTTPS origin"):
        KubernetesWorkerConfiguration(api_url="http://user:secret@localhost")
