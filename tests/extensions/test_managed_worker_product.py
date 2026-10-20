"""Explicit resident deployment through HTTP, real Temporal, PG and isolated Docker."""

import asyncio
import os
import shutil
import socket
import sys
from contextlib import suppress
from pathlib import Path
from uuid import UUID, uuid4

import docker
import httpx
import pytest
from opsmesh_sdk.development.build import build_package
from sqlalchemy import select
from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.extensions.models import ManagedDeployment
from opsmesh.extensions.schemas import extension_work
from opsmesh.extensions.workflow import ExtensionWorkflow
from opsmesh.identity.users.models import UserProfile
from tests.execution.test_product_acceptance import eventually, process
from tests.extensions.fixture_package.s14_managed import exports

pytestmark = pytest.mark.skipif(
    os.environ.get("OPSMESH_TEST_MANAGED_PRODUCT") != "1",
    reason="Real resident product opt-in",
)


def test_stop_racing_start_is_not_a_terminal_workflow_result():
    async def verify():
        calls = []

        @activity.defn(name="extensions.operate")
        async def operate(work: dict) -> dict:
            calls.append(work["action"])
            return {"status": "stopping" if len(calls) == 1 else "stopped"}

        client = await Client.connect(
            os.environ["OPSMESH_TEST_TEMPORAL_ADDRESS"],
            data_converter=pydantic_data_converter,
        )
        queue = f"s14-stop-race-{uuid4()}"
        async with Worker(
            client, task_queue=queue, workflows=[ExtensionWorkflow], activities=[operate]
        ):
            work = extension_work(uuid4(), "start_worker")
            result = await client.execute_workflow(
                ExtensionWorkflow.run, work, id=work.workflow_id, task_queue=queue
            )
            assert result == {"status": "stopped"}
            assert calls == ["start_worker", "start_worker"]
            history = await client.get_workflow_handle(work.workflow_id).fetch_history()
            await Replayer(
                workflows=[ExtensionWorkflow], data_converter=pydantic_data_converter
            ).replay_workflow(history)

    asyncio.run(verify())


def test_explicit_resident_concurrency_cancel_loss_and_drain(
    database_sessions, database_url, tmp_path
):
    client = docker.from_env()
    source = tmp_path / "package"
    shutil.copytree(Path(__file__).parent / "fixture_package", source)
    metadata = source / "pyproject.toml"
    metadata.write_text(
        metadata.read_text().replace(
            's8_acceptance = "s8_acceptance:exports"',
            's14_managed = "s14_managed:exports"',
        )
    )
    manifest = exports(None).manifest
    (source / "manifest.json").write_text(manifest.model_dump_json())
    payload = build_package(source, tmp_path / "managed.zip").read_bytes()
    deployments = []

    async def verify():
        temporal_environment = (
            WorkflowEnvironment.from_client(
                await Client.connect(os.environ["OPSMESH_TEST_TEMPORAL_ADDRESS"])
            )
            if os.environ.get("OPSMESH_TEST_TEMPORAL_ADDRESS")
            else await WorkflowEnvironment.start_local()
        )
        async with temporal_environment as temporal:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            environment = {
                **os.environ,
                "OPSMESH_DATABASE_URL": database_url,
                "OPSMESH_REDIS_URL": os.environ["OPSMESH_TEST_REDIS_URL"],
                "OPSMESH_TEMPORAL_ADDRESS": temporal.client.service_client.config.target_host,
                "OPSMESH_TEMPORAL_TASK_QUEUE": f"s14-{uuid4()}",
                "OPSMESH_STORAGE_ROOT": str(tmp_path / "storage"),
                "OPSMESH_EXTENSION_BUILDER_IMAGE": os.environ[
                    "OPSMESH_TEST_EXTENSION_BUILDER_IMAGE"
                ],
                "OPSMESH_EXTENSION_SDK_WHEEL": os.environ["OPSMESH_TEST_SDK_WHEEL"],
                "OPSMESH_TRACING_ENABLED": "false",
                "OPSMESH_OTEL_LOGS_ENABLED": "false",
                "OPSMESH_API_RATE_LIMIT_REQUESTS": "10000",
                "OPSMESH_AUTH_RATE_LIMIT_REQUESTS": "10000",
            }
            with (
                process(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "opsmesh.bootstrap.api:create_app",
                        "--factory",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                    ],
                    environment,
                    tmp_path / "api.log",
                ),
                process(
                    [sys.executable, "-m", "opsmesh.bootstrap.worker_cli"],
                    environment,
                    tmp_path / "worker.log",
                ),
            ):
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", timeout=30
                ) as api:

                    async def ready():
                        try:
                            return (await api.get("/api/v1/health")).status_code
                        except httpx.TransportError:
                            return 0

                    await eventually(ready, lambda code: code == 200, seconds=30)

                    async def call(method, path, body=None, status=200):
                        response = await api.request(method, "/api/v1" + path, json=body)
                        assert response.status_code == status, response.text
                        return response.json() if response.content else None

                    grant = await call(
                        "POST",
                        "/auth/register",
                        {
                            "email": f"s14-{uuid4()}@example.com",
                            "display_name": "S14",
                            "password": "s14-test-only-123",
                        },
                        201,
                    )
                    api.headers["X-Session-ID"] = grant["session_token"]
                    with database_sessions() as session, session.begin():
                        session.get(UserProfile, UUID(grant["user"]["user_id"])).platform_roles = [
                            "admin",
                            "member",
                        ]
                    configuration = {
                        "operations": [],
                        "hooks": [],
                        "events": [],
                        "permissions": [p.name for p in manifest.permissions],
                    }
                    for export in manifest.operations:
                        value = export.model_dump(mode="json", exclude={"kind"})
                        for field in ("input_schema", "output_schema"):
                            value[field] = getattr(export, field).model_dump_schema()
                        configuration["operations"].append(value)
                    draft = await call(
                        "POST",
                        "/capabilities/definitions",
                        {
                            "key": "s14_managed",
                            "name": "S14",
                            "declaration": {"type": "extension", "configuration": configuration},
                        },
                        201,
                    )
                    definition_id = draft["definition_id"]
                    response = await api.put(
                        f"/api/v1/capabilities/definitions/{definition_id}/draft/package",
                        content=payload,
                        headers={"Content-Type": "application/octet-stream"},
                    )
                    assert response.status_code == 200, response.text
                    await call(
                        "POST", f"/capabilities/definitions/{definition_id}/versions/1/publish"
                    )
                    with database_sessions() as session:
                        version_id = str(
                            session.scalar(
                                select(CapabilityVersion.id).where(
                                    CapabilityVersion.definition_id == UUID(definition_id),
                                )
                            )
                        )
                    install_request = {
                        "version_id": version_id,
                        "operation_id": "managed-install",
                        "permission_ceiling": configuration["permissions"],
                    }
                    installation = await call(
                        "POST", "/extensions/installations", install_request, 202
                    )
                    iid = installation["id"]
                    installation = await eventually(
                        lambda: call("GET", f"/extensions/installations/{iid}"),
                        lambda item: item["status"] in {"enabled", "failed"},
                        seconds=180,
                    )
                    assert installation["status"] == "enabled", installation
                    instance = await call(
                        "POST",
                        "/extensions/instances",
                        {
                            "installation_id": iid,
                            "permissions": configuration["permissions"],
                        },
                        201,
                    )
                    instance_id = instance["id"]

                    async def start(key):
                        deployment = await call(
                            "POST",
                            f"/extensions/instances/{instance_id}/deployments",
                            {
                                "expected_revision": instance["revision"],
                                "operation_id": key,
                            },
                            202,
                        )
                        deployments.append(deployment["id"])
                        return await eventually(
                            lambda: call("GET", f"/extensions/deployments/{deployment['id']}"),
                            lambda item: item["status"] in {"ready", "needs_attention"},
                            seconds=90,
                        )

                    deployment = await start("resident-start")
                    assert deployment["status"] == "ready", (
                        deployment,
                        (tmp_path / "worker.log").read_text()[-6000:],
                    )
                    replay = await call(
                        "POST",
                        f"/extensions/instances/{instance_id}/deployments",
                        {
                            "expected_revision": instance["revision"],
                            "operation_id": "resident-start",
                        },
                        202,
                    )
                    assert replay["id"] == deployment["id"]

                    async def invoke(key, delay=0):
                        return await call(
                            "POST",
                            f"/extensions/instances/{instance_id}/invocations",
                            {
                                "export_id": "probe",
                                "operation_id": key,
                                "arguments": {"key": key, "delay": delay},
                            },
                            202,
                        )

                    async def done(invocation):
                        result = await eventually(
                            lambda invocation=invocation: call(
                                "GET", f"/extensions/invocations/{invocation['id']}"
                            ),
                            lambda value: (
                                value["status"] in {"completed", "needs_attention", "failed"}
                            ),
                        )
                        assert result["status"] == "completed", result
                        return result

                    first, second = await asyncio.gather(
                        invoke("parallel-a", 8), invoke("parallel-b", 8)
                    )
                    for invocation in (first, second):
                        await eventually(
                            lambda invocation=invocation: call(
                                "GET", f"/extensions/invocations/{invocation['id']}"
                            ),
                            lambda item: item["status"] == "started",
                        )
                    third = await invoke("capacity-waiter")
                    waiting = await call("GET", f"/extensions/invocations/{third['id']}")
                    assert waiting["status"] == "queued", waiting
                    results = await asyncio.gather(done(first), done(second))
                    values = [result["result"]["value"] for result in results]
                    assert values[0]["pid"] == values[1]["pid"]
                    assert max(v["peak"] for v in values) == 2
                    assert all(v["uid"] == os.getuid() for v in values)
                    assert {v["invocation_id"] for v in values} == {first["id"], second["id"]}
                    assert (await done(third))["result"]["value"]["peak"] == 2
                    slow, neighbor = await asyncio.gather(
                        invoke("cancel-me", 15), invoke("neighbor", 3)
                    )
                    slow = await eventually(
                        lambda: call("GET", f"/extensions/invocations/{slow['id']}"),
                        lambda item: item["status"] == "started",
                    )
                    await call(
                        "POST",
                        f"/extensions/invocations/{slow['id']}/cancel",
                        {
                            "expected_revision": slow["revision"],
                        },
                    )
                    await done(neighbor)
                    uncertain = await eventually(
                        lambda: call("GET", f"/extensions/invocations/{slow['id']}"),
                        lambda item: item["status"] == "needs_attention",
                    )
                    await call(
                        "POST",
                        f"/extensions/invocations/{slow['id']}/reconcile",
                        {
                            "expected_revision": uncertain["revision"],
                            "outcome": "failed",
                            "evidence": {"connection_closed": True, "state_key": "cancel-me"},
                        },
                    )
                    with database_sessions() as session:
                        container_id = session.get(
                            ManagedDeployment, UUID(deployment["id"])
                        ).container_id
                    client.containers.get(container_id).kill()
                    lost = await eventually(
                        lambda: call("GET", f"/extensions/deployments/{deployment['id']}"),
                        lambda item: item["status"] == "needs_attention",
                        seconds=30,
                    )
                    with database_sessions() as session, session.begin():
                        session.get(UserProfile, UUID(grant["user"]["user_id"])).status = "disabled"

                    async def revoked_deployment_status():
                        with database_sessions() as session:
                            return session.get(ManagedDeployment, UUID(lost["id"])).status

                    await eventually(
                        revoked_deployment_status, lambda status: status == "stopped", seconds=30
                    )
                    with database_sessions() as session, session.begin():
                        session.get(UserProfile, UUID(grant["user"]["user_id"])).status = "active"
                    await call(
                        "POST",
                        f"/extensions/deployments/{lost['id']}/stop",
                        {
                            "expected_revision": lost["revision"],
                        },
                        202,
                    )
                    await eventually(
                        lambda: call("GET", f"/extensions/deployments/{lost['id']}"),
                        lambda item: item["status"] == "stopped",
                    )
                    replacement = await start("explicit-replacement")
                    assert replacement["status"] == "ready", replacement

                    async def admission(drained):
                        command = "admission.drain" if drained else "admission.resume"
                        receipt = await call(
                            "POST",
                            "/platform/operations",
                            {
                                "owner": "platform",
                                "command": command,
                                "idempotency_key": command,
                            },
                            202,
                        )
                        await eventually(
                            lambda: call("GET", f"/platform/operations/{receipt['id']}"),
                            lambda item: item["status"] == "completed",
                        )

                    await admission(True)
                    await eventually(
                        lambda: call("GET", f"/extensions/deployments/{replacement['id']}"),
                        lambda item: item["status"] == "stopped",
                    )
                    status = await call("GET", "/platform/operations/admission")
                    assert status["drained"] is True
                    assert status["active"]["extensions"] == 0, status
                    await call(
                        "POST",
                        f"/extensions/instances/{instance_id}/deployments",
                        {
                            "expected_revision": instance["revision"],
                            "operation_id": "deny-drained-start",
                        },
                        409,
                    )
                    await call(
                        "POST",
                        f"/extensions/instances/{instance_id}/invocations",
                        {"export_id": "probe", "operation_id": "deny-drained-invoke"},
                        409,
                    )
                    await admission(False)

                    # Commit start and drain together to force an undispatched
                    # deployment. The real publisher must still deliver cleanup.
                    from opsmesh.bootstrap.connections import secret_cipher
                    from opsmesh.bootstrap.extensions import ExtensionScopes
                    from opsmesh.bootstrap.platform import operation_handlers
                    from opsmesh.bootstrap.routers import ConfigurationExecutionScopeAdmission
                    from opsmesh.config import Settings
                    from opsmesh.extensions.deployment import request_start
                    from opsmesh.extensions.schemas import DeploymentRequest
                    from opsmesh.identity.provisioning import UserIdentityDirectory

                    with database_sessions() as session, session.begin():
                        api_cipher = secret_cipher(Settings())
                        session.info["dispatch_cipher"] = api_cipher
                        actor = UserIdentityDirectory().load_identity(
                            session, UUID(grant["user"]["user_id"])
                        )
                        pending = request_start(
                            session,
                            user=actor,
                            instance_id=UUID(instance_id),
                            request=DeploymentRequest(
                                expected_revision=instance["revision"],
                                operation_id="start-racing-drain",
                            ),
                            scopes=ExtensionScopes(
                                ConfigurationExecutionScopeAdmission(), api_cipher
                            ),
                        )
                        pending_id = str(pending.id)
                        deployments.append(pending_id)
                        operation_handlers(api_cipher)["platform", "admission.drain"](
                            session, {}, None, 100, actor, uuid4()
                        )
                    await eventually(
                        lambda: call("GET", f"/extensions/deployments/{pending_id}"),
                        lambda item: item["status"] == "stopped",
                        seconds=30,
                    )
                    assert not client.containers.list(
                        all=True, filters={"label": f"opsmesh.deployment={pending_id}"}
                    )
                    await call(
                        "POST",
                        "/platform/operations",
                        {
                            "owner": "platform",
                            "command": "admission.resume",
                            "idempotency_key": "resume-after-start-race",
                        },
                        202,
                    )
                    await eventually(
                        lambda: call("GET", "/platform/operations/admission"),
                        lambda item: item["drained"] is False,
                    )
                    replacement = await start("explicit-after-resume")
                    assert replacement["status"] == "ready", replacement
                    await call(
                        "POST",
                        f"/extensions/installations/{iid}/uninstall",
                        {
                            "expected_revision": installation["revision"],
                        },
                        202,
                    )
                    await eventually(
                        lambda: call("GET", f"/extensions/installations/{iid}"),
                        lambda item: item["status"] == "removed",
                    )
                    drained = await call("GET", f"/extensions/deployments/{replacement['id']}")
                    assert drained["status"] == "stopped", drained
                    from opsmesh.bootstrap.temporal import connect_temporal
                    from opsmesh.config import Settings

                    replay_client = await connect_temporal(
                        Settings(
                            temporal_address=temporal.client.service_client.config.target_host,
                        )
                    )
                    history = await replay_client.get_workflow_handle(
                        f"extension-start_worker-{deployment['id']}",
                    ).fetch_history()
                    await Replayer(
                        workflows=[ExtensionWorkflow], data_converter=replay_client.data_converter
                    ).replay_workflow(history)

    try:
        asyncio.run(verify())
    finally:
        from opsmesh.extensions.managed_process import DockerManagedWorker

        backend = DockerManagedWorker()
        for identifier in deployments:
            with suppress(Exception):
                backend.stop(identifier)
        client.close()
