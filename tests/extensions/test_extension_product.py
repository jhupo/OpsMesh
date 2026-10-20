"""Opt-in API, Temporal, Docker and independent SDK product acceptance."""

import asyncio
import json
import os
import shutil
import socket
import sys
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

import docker
import httpx
import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer

from opsmesh.extensions.workflow import ExtensionWorkflow
from opsmesh.identity.users.models import UserProfile
from opsmesh.platform.policies.redaction import PLATFORM_REDACTION

pytestmark = pytest.mark.skipif(
    os.environ.get("OPSMESH_TEST_EXTENSIONS_PRODUCT") != "1",
    reason="Real extension product acceptance opt-in",
)


def test_install_invoke_uninstall(database_sessions, database_url, tmp_path):
    from opsmesh_sdk.development.build import build_package

    from tests.execution.test_product_acceptance import eventually, process
    from tests.extensions.fixture_package.s8_acceptance import exports

    model = os.environ.get("OPSMESH_TEST_MODEL_NAME", "gpt-6-luna")
    wheel = Path(os.environ["OPSMESH_TEST_SDK_WHEEL"])
    docker_client = docker.from_env()
    builder = os.environ.get("OPSMESH_TEST_EXTENSION_BUILDER_IMAGE")
    if builder is None:
        builder = "opsmesh-s8-builder:acceptance"
        build_root = tmp_path / "base"
        build_root.mkdir()
        shutil.copy(wheel, build_root)
        (build_root / "Dockerfile").write_text(
            "FROM python:3.14-slim\nCOPY *.whl /sdk/\n"
            "RUN python -m pip install --no-cache-dir setuptools /sdk/*.whl\n"
            "RUN rm -rf /sdk\nUSER 65534:65534\nWORKDIR /tmp\n"
        )
        docker_client.images.build(path=str(build_root), tag=builder, rm=True)
    source = tmp_path / "package"
    shutil.copytree(Path(__file__).parent / "fixture_package", source)
    manifest = exports(None).manifest
    (source / "manifest.json").write_text(manifest.model_dump_json())
    package = build_package(source, tmp_path / "package.zip").read_bytes()
    containers = []

    async def verify():
        async with await WorkflowEnvironment.start_local() as temporal:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            environment = {
                **os.environ,
                "OPSMESH_DATABASE_URL": database_url,
                "OPSMESH_REDIS_URL": os.environ["OPSMESH_TEST_REDIS_URL"],
                "OPSMESH_TEMPORAL_ADDRESS": temporal.client.service_client.config.target_host,
                "OPSMESH_TEMPORAL_TASK_QUEUE": f"s8-{uuid4()}",
                "OPSMESH_STORAGE_ROOT": str(tmp_path / "storage"),
                "OPSMESH_EXTENSION_BUILDER_IMAGE": builder,
                "OPSMESH_EXTENSION_SDK_WHEEL": str(wheel),
                "OPSMESH_EXECUTION_SANDBOX_IMAGES": json.dumps(
                    [os.environ["OPSMESH_TEST_SANDBOX_IMAGE"]]
                    if os.environ.get("OPSMESH_TEST_SANDBOX_IMAGE")
                    else []
                ),
                "OPSMESH_EXECUTION_MODEL_CONTEXT_WINDOWS": json.dumps({model: 32768}),
                "OPSMESH_TRACING_ENABLED": "false",
                "OPSMESH_OTEL_LOGS_ENABLED": "false",
                "OPSMESH_API_RATE_LIMIT_REQUESTS": "10000",
                "OPSMESH_AUTH_RATE_LIMIT_REQUESTS": "10000",
            }
            environment.pop("OPSMESH_TEST_MODEL_API_KEY", None)
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
                        "--no-access-log",
                    ],
                    environment,
                    tmp_path / "api.log",
                ),
                process(
                    [sys.executable, "-m", "opsmesh.bootstrap.worker_cli"],
                    environment,
                    tmp_path / "worker.log",
                ) as worker,
            ):
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", timeout=30
                ) as api:

                    async def ready():
                        try:
                            return (await api.get("/api/v1/health")).status_code
                        except httpx.TransportError:
                            return 0

                    await eventually(ready, lambda value: value != 0, seconds=30)

                    async def call(method, path, body=None, *, status=200, **kwargs):
                        response = await api.request(method, "/api/v1" + path, json=body, **kwargs)
                        assert response.status_code == status, response.text
                        return response.json() if response.content else None

                    grant = await call(
                        "POST",
                        "/auth/register",
                        {
                            "email": f"s8-{uuid4()}@example.com",
                            "display_name": "S8 acceptance",
                            "password": "s8-test-only-123",
                        },
                        status=201,
                    )
                    user_id = grant["user"]["user_id"]
                    api.headers["X-Session-ID"] = grant["session_token"]
                    await call(
                        "POST",
                        "/extensions/installations",
                        {
                            "version_id": str(uuid4()),
                            "operation_id": "denied",
                        },
                        status=403,
                    )
                    with database_sessions() as session, session.begin():
                        from uuid import UUID

                        session.get(UserProfile, UUID(user_id)).platform_roles = ["admin", "member"]
                    connection = await call(
                        "POST",
                        "/platform/model-connections",
                        {
                            "name": "S8 model",
                            "api_key": os.environ.get(
                                "OPSMESH_TEST_MODEL_API_KEY", "unused-test-key"
                            ),
                            "base_url": os.environ.get(
                                "OPSMESH_TEST_MODEL_BASE_URL", "https://api.openai.com/v1"
                            ),
                            "default_model": model,
                            "models": [model],
                        },
                        status=201,
                    )
                    connection_id = connection["connection_id"]
                    await call(
                        "PUT",
                        f"/platform/model-connections/{connection_id}/users/{user_id}",
                        {"models": [model], "is_default": True},
                    )
                    model_ref = f"{connection_id}:{model}"
                    configuration = {
                        "operations": [],
                        "hooks": [],
                        "events": [],
                        "permissions": [p.name for p in manifest.permissions],
                    }
                    for group in ("operations", "hooks"):
                        for export in getattr(manifest, group):
                            value = export.model_dump(mode="json", exclude={"kind"})
                            for field in ("input_schema", "output_schema"):
                                if hasattr(export, field):
                                    value[field] = getattr(export, field).model_dump_schema()
                            configuration[group].append(value)
                    draft = await call(
                        "POST",
                        "/capabilities/definitions",
                        {
                            "key": "s8_acceptance",
                            "name": "S8 acceptance",
                            "declaration": {"type": "extension", "configuration": configuration},
                        },
                        status=201,
                    )
                    definition_id = draft["definition_id"]
                    attached = await api.put(
                        f"/api/v1/capabilities/definitions/{definition_id}/draft/package",
                        content=package,
                        headers={"Content-Type": "application/octet-stream"},
                    )
                    assert attached.status_code == 200, attached.text
                    await call(
                        "POST", f"/capabilities/definitions/{definition_id}/versions/1/publish"
                    )
                    with database_sessions() as session:
                        from sqlalchemy import select

                        from opsmesh.capabilities.models import CapabilityVersion

                        version_id = str(
                            session.scalar(
                                select(CapabilityVersion.id).where(
                                    CapabilityVersion.definition_id == UUID(definition_id)
                                )
                            )
                        )
                    installation = await call(
                        "POST",
                        "/extensions/installations",
                        {
                            "version_id": version_id,
                            "operation_id": "acceptance-install",
                            "permission_ceiling": configuration["permissions"],
                        },
                        status=202,
                    )
                    iid = installation["id"]
                    installation = await eventually(
                        lambda: call("GET", f"/extensions/installations/{iid}"),
                        lambda item: item["status"] in {"enabled", "failed"},
                        seconds=180,
                    )
                    assert installation["status"] == "enabled", installation
                    with database_sessions() as session, session.begin():
                        session.get(UserProfile, UUID(user_id)).platform_roles = ["member"]
                    for action in ("disable", "uninstall"):
                        await call(
                            "POST",
                            f"/extensions/installations/{iid}/{action}",
                            {"expected_revision": installation["revision"]},
                            status=403,
                        )
                    with database_sessions() as session, session.begin():
                        session.get(UserProfile, UUID(user_id)).platform_roles = ["admin", "member"]
                    env = await call(
                        "POST",
                        "/orchestration/environments",
                        {
                            "image": installation["artifact"],
                        },
                        status=202,
                        params={"idempotency_key": "acceptance-environment"},
                    )
                    eid = env["environment_id"]
                    await eventually(
                        lambda: call("GET", f"/orchestration/environments/{eid}"),
                        lambda value: value["status"] == "ready",
                    )
                    from opsmesh.bootstrap.connections import secret_cipher
                    from opsmesh.config import Settings
                    from opsmesh.orchestration.runtime.models import Environment

                    with database_sessions() as session:
                        state = secret_cipher(Settings()).decrypt_payload(
                            session.get(Environment, UUID(eid)).encrypted_state
                        )
                        containers.append(state["container_id"])
                    instance = await call(
                        "POST",
                        "/extensions/instances",
                        {
                            "installation_id": iid,
                            "environment_id": eid,
                            "model_connections": [model_ref],
                            "configuration": {"model_connection": model_ref},
                            "permissions": configuration["permissions"],
                        },
                        status=201,
                    )

                    async def invoke(export, operation, arguments):
                        receipt = await call(
                            "POST",
                            f"/extensions/instances/{instance['id']}/invocations",
                            {
                                "export_id": export,
                                "operation_id": operation,
                                "arguments": arguments,
                            },
                            status=202,
                        )
                        return await eventually(
                            lambda: call("GET", f"/extensions/invocations/{receipt['id']}"),
                            lambda value: (
                                value["status"] in {"completed", "failed", "needs_attention"}
                            ),
                        )

                    result = await invoke("state", "acceptance-state", {"answer": 42})
                    if result["status"] != "completed":
                        from opsmesh.extensions.models import HostCallReceipt
                        from opsmesh.shared.security.redaction import redact_sensitive_payload

                        with database_sessions() as session:
                            for host_receipt in session.scalars(
                                select(HostCallReceipt).where(
                                    HostCallReceipt.invocation_id == UUID(result["id"])
                                )
                            ):
                                print(
                                    "Host receipt",
                                    host_receipt.status,
                                    redact_sensitive_payload(
                                        secret_cipher(Settings()).decrypt_payload(
                                            host_receipt.result_ciphertext or ""
                                        ),
                                        rules=PLATFORM_REDACTION,
                                    )
                                    if host_receipt.result_ciphertext
                                    else {},
                                )
                    assert result["status"] == "completed", result
                    limits = docker_client.containers.get(containers[-1]).attrs["HostConfig"]
                    assert limits["Memory"] == 256 * 1048576 and limits["PidsLimit"] == 64
                    assert limits["NanoCpus"] == 1000000000
                    assert result["result"]["value"]["stored"] == {"answer": 42}
                    replay = await invoke("state", "acceptance-state", {"answer": 42})
                    assert replay["id"] == result["id"]
                    history = await temporal.client.get_workflow_handle(
                        f"extension-invoke-{result['id']}"
                    ).fetch_history()
                    from opsmesh.bootstrap.temporal import connect_temporal

                    replay_client = await connect_temporal(
                        Settings(temporal_address=temporal.client.service_client.config.target_host)
                    )
                    await Replayer(
                        workflows=[ExtensionWorkflow], data_converter=replay_client.data_converter
                    ).replay_workflow(history)
                    from opsmesh.extensions.models import ExtensionEvent
                    from opsmesh.temporal.dispatch.models import TemporalOutboxEntry

                    with database_sessions() as session:
                        event_id = session.scalar(
                            select(ExtensionEvent.id).where(
                                ExtensionEvent.invocation_id == UUID(result["id"])
                            )
                        )

                    from uuid import NAMESPACE_URL, uuid5

                    dispatch_id = uuid5(NAMESPACE_URL, f"source:extension:{event_id}:")

                    async def event_dispatched():
                        with database_sessions() as session:
                            return (
                                session.get(TemporalOutboxEntry, dispatch_id).published_at
                                is not None
                            )

                    await eventually(event_dispatched, bool)
                    publication = await replay_client.get_workflow_handle(
                        f"source-event:{dispatch_id}"
                    ).result()
                    assert publication["accepted"] == 0
                    namespace = result["namespace_id"]
                    audit_result = await invoke("audit", "delegated-audit", {})
                    assert audit_result["status"] == "completed", audit_result
                    assert audit_result["result"]["value"]["action"] == "s8_acceptance.state.saved"
                    await call(
                        "POST",
                        "/platform/audit",
                        {
                            "action": "state.saved",
                            "target_type": "value",
                            "target_id": "answer",
                            "summary": "Forged",
                            "idempotency_key": "forged",
                        },
                        status=401,
                        headers={
                            "X-OpsMesh-Installation-Id": iid,
                            "X-OpsMesh-Instance-Id": instance["id"],
                            "X-OpsMesh-Invocation-Id": audit_result["id"],
                            "X-OpsMesh-Namespace": "s8_acceptance",
                        },
                    )
                    page = await call("GET", f"/extensions/namespaces/{namespace}/state")
                    assert page["entries"]
                    model_session = await call(
                        "POST",
                        "/orchestration/sessions",
                        {},
                        status=201,
                    )
                    frozen = {
                        "instance_id": instance["id"],
                        "installation_id": iid,
                        "configuration_revision": instance["revision"],
                        "artifact": installation["artifact"],
                        "export_id": "guard",
                    }
                    guarded = await call(
                        "POST",
                        "/orchestration/runs",
                        {
                            "session_id": model_session["session_id"],
                            "input": "Rejected input",
                            "entry_agent": "main",
                            "agents": {
                                "main": {
                                    "name": "Guarded",
                                    "model": {"connection_id": connection_id, "name": model},
                                }
                            },
                            "hooks": [
                                {**frozen, "binding_id": "guard", "stages": ["input_guardrail"]}
                            ],
                        },
                        status=202,
                        params={"idempotency_key": "guarded-run"},
                    )
                    guarded = await eventually(
                        lambda: call("GET", f"/orchestration/runs/{guarded['run_id']}"),
                        lambda value: value["status"] in {"failed", "succeeded", "needs_attention"},
                    )
                    assert guarded["status"] == "failed" and guarded["output"] is None, guarded
                    if os.environ.get("OPSMESH_TEST_MODEL_API_KEY"):
                        answer = await invoke("model", "host-model", {})
                        assert answer["status"] == "completed", answer
                        assert answer["result"]["value"]["usage"]["total_tokens"] > 0
                        session2 = await call(
                            "POST",
                            "/orchestration/sessions",
                            {},
                            status=201,
                        )
                        request = {
                            "session_id": session2["session_id"],
                            "input": (
                                "Call s8_state exactly once with an empty object, then reply S8_OK."
                            ),
                            "entry_agent": "main",
                            "agents": {
                                "main": {
                                    "name": "Installed tool",
                                    "model": {
                                        "connection_id": connection_id,
                                        "name": model,
                                        "max_output_tokens": 2048,
                                        "reasoning_effort": os.environ.get(
                                            "OPSMESH_TEST_MODEL_REASONING_EFFORT"
                                        ),
                                    },
                                    "tools": [
                                        {
                                            "name": "s8_state",
                                            "input_schema": manifest.operations[
                                                0
                                            ].input_schema.model_dump_schema(),
                                            "approval_required": False,
                                            "extension": {**frozen, "export_id": "state"},
                                        }
                                    ],
                                }
                            },
                            "hooks": [
                                {
                                    **frozen,
                                    "export_id": "trace",
                                    "binding_id": "trace",
                                    "stages": [stage.value for stage in manifest.hooks[1].stages],
                                }
                            ],
                        }
                        tool_run = await call(
                            "POST",
                            "/orchestration/runs",
                            request,
                            status=202,
                            params={"idempotency_key": "installed-tool-run"},
                        )
                        tool_run = await eventually(
                            lambda: call("GET", f"/orchestration/runs/{tool_run['run_id']}"),
                            lambda value: (
                                value["status"] in {"failed", "succeeded", "needs_attention"}
                            ),
                        )
                        if tool_run["status"] != "succeeded":
                            history = await temporal.client.get_workflow_handle(
                                f"run:{tool_run['run_id']}"
                            ).fetch_history()
                            for event in history.events:
                                if event.HasField("activity_task_failed_event_attributes"):
                                    failure = event.activity_task_failed_event_attributes.failure
                                    error = await replay_client.data_converter.decode_failure(
                                        failure
                                    )
                                    print(str(error)[:1000], error.failure.stack_trace[-2000:])
                                    cause = error.__cause__
                                    while cause is not None:
                                        print(type(cause).__name__, str(cause)[:1000])
                                        cause = cause.__cause__
                        assert tool_run["status"] == "succeeded", tool_run
                        from temporalio.openai_agents import OpenAIAgentsPlugin

                        from opsmesh.orchestration.runs.agent_workflow import ExecutionRunWorkflow

                        tool_history = await replay_client.get_workflow_handle(
                            f"run:{tool_run['run_id']}"
                        ).fetch_history()
                        await Replayer(
                            workflows=[ExecutionRunWorkflow],
                            plugins=[OpenAIAgentsPlugin()],
                            data_converter=replay_client.data_converter,
                        ).replay_workflow(tool_history)
                        from opsmesh.extensions.models import Invocation

                        with database_sessions() as session:
                            stages = [
                                secret_cipher(Settings()).decrypt_payload(row.input_ciphertext)[
                                    "stage"
                                ]
                                for row in session.scalars(
                                    select(Invocation).where(
                                        Invocation.snapshot["run_id"].astext == tool_run["run_id"],
                                        Invocation.kind == "hook",
                                    )
                                )
                            ]
                        assert {
                            "model.before",
                            "model.after",
                            "tool.before",
                            "tool.after",
                            "finalize",
                        } <= set(stages)
                        blocked_session = await call(
                            "POST", "/orchestration/sessions", {}, status=201
                        )
                        blocked = await call(
                            "POST",
                            "/orchestration/runs",
                            {
                                "session_id": blocked_session["session_id"],
                                "input": "Reply only S8_OUTPUT_CANDIDATE",
                                "entry_agent": "main",
                                "agents": {
                                    "main": {
                                        "name": "Output guard",
                                        "model": {
                                            "connection_id": connection_id,
                                            "name": model,
                                            "max_output_tokens": 2048,
                                            "reasoning_effort": os.environ.get(
                                                "OPSMESH_TEST_MODEL_REASONING_EFFORT"
                                            ),
                                        },
                                    }
                                },
                                "hooks": [
                                    {
                                        **frozen,
                                        "export_id": "output_guard",
                                        "binding_id": "output_guard",
                                        "stages": ["output_guardrail"],
                                    }
                                ],
                            },
                            status=202,
                            params={"idempotency_key": "output-guard-run"},
                        )
                        blocked = await eventually(
                            lambda: call("GET", f"/orchestration/runs/{blocked['run_id']}"),
                            lambda value: (
                                value["status"] in {"failed", "succeeded", "needs_attention"}
                            ),
                        )
                        assert blocked["status"] == "failed" and blocked["output"] is None
                        history = await call(
                            "GET",
                            f"/orchestration/sessions/{blocked_session['session_id']}/history",
                        )
                        assert not any(item.get("role") == "assistant" for item in history["items"])
                        from opsmesh.identity.quotas.models import UserTokenAccount

                        with database_sessions() as session:
                            account = session.get(UserTokenAccount, UUID(user_id))
                            assert account.used_tokens > 0 and account.reserved_tokens == 0
                    cancelled = await call(
                        "POST",
                        f"/extensions/instances/{instance['id']}/invocations",
                        {
                            "export_id": "sleep",
                            "operation_id": "cancelled-sleep",
                        },
                        status=202,
                    )
                    cancelled = await eventually(
                        lambda: call("GET", f"/extensions/invocations/{cancelled['id']}"),
                        lambda value: value["status"] == "started",
                    )

                    async def sleep_record():
                        values = await call("GET", f"/extensions/namespaces/{namespace}/state")
                        return next(
                            (row for row in values["entries"] if row["key"] == "sleep_count"), None
                        )

                    await eventually(sleep_record, lambda value: value and value["value"] == 1)
                    await call(
                        "POST",
                        f"/extensions/invocations/{cancelled['id']}/cancel",
                        {
                            "expected_revision": cancelled["revision"],
                        },
                    )
                    await eventually(
                        lambda: call("GET", f"/extensions/invocations/{cancelled['id']}"),
                        lambda value: value["status"] == "cancelled",
                    )
                    if os.environ.get("OPSMESH_TEST_S9_S10_PRODUCT") == "1":
                        from tests.orchestration.product_scenarios import (
                            verify_configurations_and_flows,
                        )

                        original_container = containers[-1]
                        await verify_configurations_and_flows(
                            api=api,
                            call=call,
                            database_sessions=database_sessions,
                            owner_user_id=user_id,
                            flow_instance=instance,
                            installation=installation,
                            version_id=version_id,
                            model_connection=connection_id,
                            model_ref=model_ref,
                            model_name=model,
                            replay_client=replay_client,
                            containers=containers,
                        )
                    else:
                        original_container = containers[-1]
                    interrupted = await call(
                        "POST",
                        f"/extensions/instances/{instance['id']}/invocations",
                        {"export_id": "sleep", "operation_id": "interrupted-sleep"},
                        status=202,
                    )
                    interrupted = await eventually(
                        lambda: call("GET", f"/extensions/invocations/{interrupted['id']}"),
                        lambda value: value["status"] == "started",
                    )

                    await eventually(sleep_record, lambda value: value and value["value"] == 2)
                    worker.kill()
                    worker.wait(10)
                    with process(
                        [sys.executable, "-m", "opsmesh.bootstrap.worker_cli"],
                        environment,
                        tmp_path / "worker-restarted.log",
                    ):
                        interrupted = await eventually(
                            lambda: call("GET", f"/extensions/invocations/{interrupted['id']}"),
                            lambda value: value["status"] == "needs_attention",
                            seconds=60,
                        )
                        assert (await sleep_record())["value"] == 2
                        native = docker_client.containers.get(original_container)
                        assert not any(
                            "opsmesh_sdk.runtime.loading" in str(row)
                            for row in native.top(ps_args="-eo pid,args")["Processes"]
                        )
                        operation = f"extension:{interrupted['id']}"
                        receipt = await call(
                            "GET", f"/orchestration/environments/{eid}/operations/{operation}"
                        )
                        await call(
                            "POST",
                            f"/orchestration/environments/{eid}/operations/{operation}/reconcile",
                            {
                                "command_id": str(uuid4()),
                                "expected_revision": receipt["revision"],
                                "unresolved_operations": [operation],
                                "confirmed_status": "ready",
                                "evidence": {"process_exited": True, "state_write_count": 2},
                            },
                        )
                        await call(
                            "POST",
                            f"/extensions/invocations/{interrupted['id']}/reconcile",
                            {
                                "expected_revision": interrupted["revision"],
                                "outcome": "failed",
                                "evidence": {"process_exited": True, "state_write_count": 2},
                            },
                        )
                        await call(
                            "POST",
                            f"/extensions/installations/{iid}/uninstall",
                            {
                                "expected_revision": installation["revision"],
                            },
                            status=202,
                        )
                        await eventually(
                            lambda: call("GET", f"/extensions/installations/{iid}"),
                            lambda value: value["status"] == "removed",
                        )
                    assert (await call("GET", f"/extensions/namespaces/{namespace}/state"))[
                        "entries"
                    ]
                    preview = await call(
                        "GET", f"/extensions/namespaces/{namespace}/cleanup-preview"
                    )
                    await call(
                        "POST",
                        f"/extensions/namespaces/{namespace}/cleanup",
                        {
                            "expected_revision": preview["revision"],
                            "preview_digest": preview["preview_digest"],
                        },
                    )
                    assert not (await call("GET", f"/extensions/namespaces/{namespace}/state"))[
                        "entries"
                    ]

    try:
        asyncio.run(verify())
    finally:
        for identifier in containers:
            with suppress(docker.errors.NotFound):
                docker_client.containers.get(identifier).remove(force=True)
        docker_client.close()
