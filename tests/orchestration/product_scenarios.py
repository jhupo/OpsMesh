"""S9/S10 acceptance through actual HTTP, installed packages, Worker and SDK."""

import base64
import hashlib
import hmac
import os
import time
from uuid import UUID, uuid4

from sqlalchemy import select
from temporalio.worker import Replayer

from opsmesh.bootstrap.connections import secret_cipher
from opsmesh.capabilities.models import CapabilityVersion
from opsmesh.capabilities.packages import build_package_archive
from opsmesh.config import Settings
from opsmesh.orchestration.runs.flow_workflow import FlowWorkflow
from opsmesh.orchestration.runs.models import FlowRun, Run, StepAttempt
from opsmesh.orchestration.runtime.models import Environment, MCPConnection
from opsmesh.platform.policies.redaction import PLATFORM_REDACTION
from tests.execution.test_product_acceptance import eventually


async def verify_configurations_and_flows(
    *,
    api,
    call,
    database_sessions,
    owner_user_id,
    flow_instance,
    installation,
    version_id,
    model_connection,
    model_ref,
    model_name,
    replay_client,
    containers,
):
    definition = await call(
        "POST",
        "/orchestration/definitions",
        {
            "name": "Log analyst",
            "kind": "agent_graph",
            "spec": {
                "entry_agent": "main",
                "agents": {
                    "main": {
                        "name": "Analyst",
                        "instructions": (
                            "Call project_probe and project_tool once, then reply PROJECT_OK."
                        ),
                        "tools": ["project_tool"],
                        "mcp": ["project_mcp"],
                    }
                },
            },
        },
        status=201,
    )
    definition = await call(
        "POST",
        f"/orchestration/definitions/{definition['id']}/publish",
        {"expected_revision": 1},
    )
    configuration = await call(
        "POST",
        "/orchestration/configurations",
        {"name": "S9-S10 assembly", "definition_revision_id": definition["id"]},
        status=201,
        params={"idempotency_key": "acceptance-configuration"},
    )
    assert configuration["owner_user_id"] == owner_user_id
    configuration_id = configuration["id"]
    base = f"/orchestration/configurations/{configuration_id}"
    environment = await call(
        "POST",
        "/orchestration/environments",
        {"configuration_id": configuration_id, "image": installation["artifact"]},
        status=202,
        params={"idempotency_key": "project-environment"},
    )
    eid = environment["environment_id"]
    await eventually(
        lambda: call("GET", f"/orchestration/environments/{eid}"),
        lambda item: item["status"] == "ready",
    )
    with database_sessions() as session:
        state = secret_cipher(Settings()).decrypt_payload(
            session.get(Environment, UUID(eid)).encrypted_state
        )
        containers.append(state["container_id"])
    instance = await call(
        "POST",
        "/extensions/instances",
        {
            "installation_id": installation["id"],
            "environment_id": eid,
            "configuration_id": configuration_id,
            "permissions": installation["permission_ceiling"],
            "model_connections": [model_ref],
            "configuration": {"model_connection": model_ref},
        },
        status=201,
    )
    bindings = "/orchestration/capability-bindings"
    tool = await call(
        "POST",
        bindings,
        {
            "name": "project_tool",
            "version_id": version_id,
            "configuration": {"instance_id": instance["id"], "export_id": "probe"},
        },
        status=201,
    )
    hook = await call(
        "POST",
        bindings,
        {
            "name": "project_trace",
            "version_id": version_id,
            "configuration": {"instance_id": instance["id"], "export_id": "trace"},
        },
        status=201,
    )
    mcp_template = await call(
        "POST",
        "/capabilities/definitions",
        {
            "key": "project_mcp",
            "name": "Project MCP",
            "declaration": {
                "type": "mcp",
                "configuration": {
                    "transport": "stdio",
                    "command": ["python", "-m", "s9_mcp"],
                    "environment": {"EXAMPLE_LABEL": "${label}"},
                    "parameters_schema": {
                        "type": "object",
                        "required": ["label"],
                        "properties": {"label": {"type": "string"}},
                    },
                    "tools": [{"name": "project_probe"}],
                },
            },
        },
        status=201,
    )
    from pathlib import Path

    mcp_source = (
        Path(__file__).parents[1] / "extensions/fixture_package/s9_mcp.py"
    ).read_bytes()
    package_response = await api.put(
        f"/api/v1/capabilities/definitions/{mcp_template['definition_id']}/draft/package",
        content=build_package_archive(
            {"s9_mcp.py": mcp_source}
        ),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert package_response.status_code == 200, package_response.text
    await call(
        "POST", f"/capabilities/definitions/{mcp_template['definition_id']}/versions/1/publish"
    )
    with database_sessions() as session:
        mcp_version = str(
            session.scalar(
                select(CapabilityVersion.id).where(
                    CapabilityVersion.definition_id == UUID(mcp_template["definition_id"])
                )
            )
        )
    mcp_binding = await call(
        "POST",
        bindings,
        {
            "name": "project_mcp",
            "version_id": mcp_version,
            "configuration": {"parameters": {"label": "s8-marker"}},
        },
        status=201,
    )
    # MCP dependencies belong to its explicitly selected compute image; the
    # installed extension artifact intentionally contains only its own dependencies.
    mcp_environment = await call(
        "POST",
        "/orchestration/environments",
        {
            "configuration_id": configuration_id,
            "image": os.environ["OPSMESH_TEST_SANDBOX_IMAGE"],
        },
        status=202,
        params={"idempotency_key": "configuration-mcp-environment"},
    )
    mcp_eid = mcp_environment["environment_id"]
    await eventually(
        lambda: call("GET", f"/orchestration/environments/{mcp_eid}"),
        lambda item: item["status"] == "ready",
    )
    with database_sessions() as session:
        state = secret_cipher(Settings()).decrypt_payload(
            session.get(Environment, UUID(mcp_eid)).encrypted_state
        )
        containers.append(state["container_id"])
    uploaded = await call(
        "POST",
        f"/orchestration/environments/{mcp_eid}/files/upload",
        {"path": "s9_mcp.py", "content_base64": base64.b64encode(mcp_source).decode()},
        status=202,
        params={"idempotency_key": "prepare-configuration-mcp"},
    )
    uploaded = await eventually(
        lambda: call(
            "GET",
            f"/orchestration/environments/{mcp_eid}/operations/{uploaded['operation_id']}",
        ),
        lambda item: item["status"] in {"completed", "uncertain"},
    )
    assert uploaded["status"] == "completed", uploaded
    mcp_binding = await call(
        "POST",
        bindings + f"/{mcp_binding['id']}/connection",
        {
            "expected_revision": 1,
            "configuration_id": configuration_id,
            "environment_id": mcp_eid,
            "transport": "stdio",
        },
        status=202,
        params={"idempotency_key": "project-mcp-connect"},
    )
    mcp_id = mcp_binding["configuration"]["mcp_connection_id"]
    mcp = await eventually(
        lambda: call("GET", f"/orchestration/mcp-connections/{mcp_id}"),
        lambda item: item["status"] in {"ready", "needs_attention"},
    )
    assert mcp["status"] == "ready", mcp
    config = {
        "capabilities": {
            "project_mcp": {"binding_id": mcp_binding["id"]},
            "project_tool": {"binding_id": tool["id"]},
        },
        "hooks": [
            {"binding_id": hook["id"], "stages": ["model.before", "model.after", "finalize"]}
        ],
        "models": {
            "default": {
                "connection_id": model_connection,
                "name": model_name,
                "max_output_tokens": 2048,
                "reasoning_effort": os.environ.get("OPSMESH_TEST_MODEL_REASONING_EFFORT"),
            }
        },
        "environment_id": eid,
    }
    first = await call("PUT", base, {"expected_revision": 0, "specification": config})
    assert first["number"] == 1
    current = (await call("GET", base + "/revisions"))[0]
    assert current["digest"] == first["digest"]
    from opsmesh_sdk.client import PlatformClient

    async with PlatformClient(str(api.base_url), session_id=api.headers["X-Session-ID"]) as client:
        generated = await client.group("orchestration").configurations.revisions.list(
            UUID(configuration_id)
        )
        assert generated[0].digest == current["digest"] and generated[0].number == 1
    second = await call(
        "PUT", base, {"expected_revision": 1, "specification": current["specification"]}
    )
    assert second["number"] == 2 and second["digest"] == first["digest"]
    await call(
        "PUT",
        base,
        {"expected_revision": 1, "specification": config},
        status=409,
    )

    async def approve_calls(run_ids):
        inbox = await call("GET", "/platform/approvals", params={"status": "pending"})
        for approval in inbox["approvals"]:
            if any(approval["target_id"].startswith(f"{identifier}:") for identifier in run_ids):
                await call(
                    "POST",
                    f"/platform/approvals/{approval['approval_id']}/approve",
                    {"idempotency_key": f"product-{approval['approval_id']}"},
                )

    async def read_run(identifier):
        value = await call("GET", f"/orchestration/runs/{identifier}")
        if value["status"] == "waiting_approval":
            await approve_calls([identifier])
        return value

    if os.environ.get("OPSMESH_TEST_MODEL_API_KEY"):
        direct = await call(
            "POST",
            base + "/runs",
            {"configuration_revision": 2, "input": "PROJECT_OK"},
            status=202,
            params={"idempotency_key": "project-direct"},
        )
        direct = await eventually(
            lambda: read_run(direct["run_id"]),
            lambda item: item["status"] in {"succeeded", "failed", "needs_attention"},
        )
        assert direct["status"] == "succeeded", direct
        from opsmesh.extensions.models import Invocation
        from opsmesh.orchestration.runs.models import OperationReceipt

        with database_sessions() as session:
            receipts = list(
                session.scalars(
                    select(OperationReceipt).where(
                        OperationReceipt.run_id == UUID(direct["run_id"])
                    )
                )
            )
            assert {"model", "tool", "mcp"} <= {row.kind for row in receipts}
            assert all(row.status == "completed" and row.accounted for row in receipts)
            assert session.scalar(
                select(Invocation.id)
                .where(
                    Invocation.kind == "hook",
                    Invocation.export_id == "trace",
                    Invocation.snapshot["run_id"].astext == direct["run_id"],
                    Invocation.status == "completed",
                )
                .limit(1)
            )

    instance = flow_instance

    async def publish(name, nodes, output=None, limits=None):
        draft = await call(
            "POST",
            "/orchestration/definitions",
            {
                "name": name,
                "spec": {
                    "graph": {"nodes": nodes, "output": output or {}},
                    **({"limits": limits} if limits else {}),
                },
            },
            status=201,
        )
        return await call(
            "POST",
            f"/orchestration/definitions/{draft['id']}/publish",
            {"expected_revision": draft["revision"]},
        )

    simple = await publish(
        "Trigger target",
        [
            {
                "key": "answer",
                "kind": "condition",
                "condition": {"operator": "eq", "left": 1, "right": 1},
            }
        ],
        {"$ref": "steps.answer"},
    )
    event_trigger = await call(
        "POST",
        "/orchestration/triggers",
        {
            "revision_id": simple["id"],
            "kind": "event",
            "configuration": {
                "source_kind": "extension",
                "instance_id": instance["id"],
                "event_type": "s8_acceptance.changed",
            },
        },
        status=201,
    )
    operation = {
        "instance_id": instance["id"],
        "installation_id": installation["id"],
        "configuration_revision": instance["revision"],
        "artifact": installation["artifact"],
        "export_id": "state",
    }
    revision = await publish(
        "Owned operation and subflow",
        [
            {
                "key": "operation",
                "kind": "operation",
                "export": operation,
                "arguments": {"value": 9},
            },
            {
                "key": "child",
                "kind": "subflow",
                "depends_on": ["operation"],
                "revision_id": simple["id"],
            },
            *(
                [
                    {
                        "key": "agent",
                        "kind": "agent",
                        "depends_on": ["child"],
                        "configuration_revision_id": second["id"],
                        "arguments": {"input": "PROJECT_OK"},
                    }
                ]
                if os.environ.get("OPSMESH_TEST_MODEL_API_KEY")
                else []
            ),
        ],
        {"$ref": "steps.child"},
    )
    created = await call(
        "POST",
        "/orchestration/flows",
        {"revision_id": revision["id"]},
        status=202,
        params={"idempotency_key": "project-flow"},
    )

    async def read_flow():
        with database_sessions() as session:
            run_ids = list(
                session.scalars(
                    select(StepAttempt.child_id).where(
                        StepAttempt.run_id == UUID(created["id"]),
                        StepAttempt.child_kind == "agent",
                        StepAttempt.child_id.is_not(None),
                    )
                )
            )
        if run_ids:
            await approve_calls(run_ids)
        return await call("GET", f"/orchestration/flows/{created['id']}")

    flow = await eventually(
        read_flow,
        lambda item: item["status"] in {"succeeded", "failed", "needs_attention"},
    )
    assert flow["status"] == "succeeded" and flow["output"] is True, flow
    history = await replay_client.get_workflow_handle(f"flow:{flow['id']}:1").fetch_history()
    await Replayer(
        workflows=[FlowWorkflow], data_converter=replay_client.data_converter
    ).replay_workflow(history)
    with database_sessions() as session:
        root = session.get(FlowRun, UUID(flow["id"]))
        assert root.started_steps >= 4 and root.active_steps == 0
        if os.environ.get("OPSMESH_TEST_MODEL_API_KEY"):
            assert root.allocated_tokens > 0
    async with PlatformClient(str(api.base_url), session_id=api.headers["X-Session-ID"]) as client:
        generated_flow = await client.group("orchestration").flows.get(UUID(flow["id"]))
        assert generated_flow.status == "succeeded" and generated_flow.output is True

    # A real installed planner receipt is approved by governance and replayed by Temporal.
    graph = {
        "nodes": [
            {
                "key": "approved",
                "kind": "condition",
                "condition": {"operator": "eq", "left": 7, "right": 7},
            }
        ],
        "output": {"$ref": "steps.approved"},
    }
    from opsmesh.orchestration.definitions.contracts import FlowGraph

    graph = FlowGraph.model_validate(graph).model_dump(mode="json")
    planner = await call(
        "POST",
        f"/extensions/instances/{instance['id']}/invocations",
        {"export_id": "plan", "operation_id": "s10-plan", "arguments": {"graph": graph}},
        status=202,
    )
    planner = await eventually(
        lambda: call("GET", f"/extensions/invocations/{planner['id']}"),
        lambda item: item["status"] in {"completed", "failed", "needs_attention"},
    )
    assert planner["status"] == "completed", planner
    expandable = await publish(
        "Approved expansion",
        [
            {
                "key": "expand",
                "kind": "expand",
                "allowed_kinds": ["condition"],
                "approval_required": True,
            }
        ],
        {"$ref": "steps.expand"},
    )
    expanded = await call(
        "POST",
        "/orchestration/flows",
        {"revision_id": expandable["id"]},
        status=202,
        params={"idempotency_key": "approved-expansion"},
    )
    await eventually(
        lambda: call("GET", f"/orchestration/flows/{expanded['id']}/steps"),
        lambda rows: rows and rows[0]["status"] == "waiting",
    )
    expanded = await call("GET", f"/orchestration/flows/{expanded['id']}")
    proposal = {
        "command_id": str(uuid4()),
        "expected_revision": expanded["revision"],
        "step_path": "expand",
        "source_invocation_id": planner["id"],
        "graph": graph,
    }
    await call("POST", f"/orchestration/flows/{expanded['id']}/plans", proposal, status=403)
    review = await call(
        "POST", f"/orchestration/flows/{expanded['id']}/plan-reviews", proposal, status=201
    )
    await call(
        "POST",
        f"/platform/approvals/{review['approval_id']}/approve",
        {"reason": "Product acceptance", "idempotency_key": "s10-plan-approval"},
    )
    await call(
        "POST",
        f"/orchestration/flows/{expanded['id']}/plans",
        {**proposal, "approval_id": review["approval_id"]},
    )
    expanded = await eventually(
        lambda: call("GET", f"/orchestration/flows/{expanded['id']}"),
        lambda item: item["status"] in {"succeeded", "failed", "needs_attention"},
    )
    assert expanded["status"] == "succeeded" and expanded["output"] is True, expanded
    history = await replay_client.get_workflow_handle(f"flow:{expanded['id']}:1").fetch_history()
    await Replayer(
        workflows=[FlowWorkflow], data_converter=replay_client.data_converter
    ).replay_workflow(history)

    # Installed code submits through host.jobs; only the single FlowRun reference is returned.
    job_configuration = await call(
        "POST",
        "/orchestration/configurations",
        {"name": "Host job", "definition_revision_id": simple["id"], "specification": {}},
        status=201,
        params={"idempotency_key": "host-job-configuration"},
    )
    job_environment = await call(
        "POST",
        "/orchestration/environments",
        {"configuration_id": job_configuration["id"], "image": installation["artifact"]},
        status=202,
        params={"idempotency_key": "host-job-environment"},
    )
    job_eid = job_environment["environment_id"]
    await eventually(
        lambda: call("GET", f"/orchestration/environments/{job_eid}"),
        lambda item: item["status"] == "ready",
    )
    with database_sessions() as session:
        state = secret_cipher(Settings()).decrypt_payload(
            session.get(Environment, UUID(job_eid)).encrypted_state
        )
        containers.append(state["container_id"])
    job_instance = await call(
        "POST",
        "/extensions/instances",
        {
            "installation_id": installation["id"],
            "environment_id": job_eid,
            "configuration_id": job_configuration["id"],
            "permissions": installation["permission_ceiling"],
        },
        status=201,
    )
    job = await call(
        "POST",
        f"/extensions/instances/{job_instance['id']}/invocations",
        {"export_id": "job", "operation_id": "s10-job", "arguments": {"revision_id": simple["id"]}},
        status=202,
    )
    job = await eventually(
        lambda: call("GET", f"/extensions/invocations/{job['id']}"),
        lambda item: item["status"] in {"completed", "failed", "needs_attention"},
    )
    if job["status"] != "completed":
        from opsmesh.extensions.models import HostCallReceipt
        from opsmesh.shared.security.redaction import redact_sensitive_payload

        with database_sessions() as session:
            for row in session.scalars(
                select(HostCallReceipt).where(HostCallReceipt.invocation_id == UUID(job["id"]))
            ):
                print(
                    "Job host receipt",
                    row.status,
                    redact_sensitive_payload(
                        secret_cipher(Settings()).decrypt_payload(row.result_ciphertext),
                        rules=PLATFORM_REDACTION,
                    )
                    if row.result_ciphertext
                    else {},
                )
    assert job["status"] == "completed", job
    assert set(job["result"]["value"]) <= {"flow_run_id", "definition_revision"}
    job_id = job["result"]["value"]["flow_run_id"]
    await eventually(
        lambda: call("GET", f"/orchestration/flows/{job_id}"),
        lambda item: item["status"] == "succeeded",
    )
    job_base = f"/orchestration/configurations/{job_configuration['id']}"
    await call("POST", job_base + "/close", {"expected_revision": 1}, status=202)
    await eventually(
        lambda: call("GET", job_base + "/close"),
        lambda item: item["status"] == "completed",
    )

    # Real signed webhook and native Schedule both create fixed revisions.
    webhook = await call(
        "POST",
        "/orchestration/triggers",
        {
            "revision_id": simple["id"],
            "kind": "webhook",
            "configuration": {},
            "webhook_secret": "product-acceptance-signature-" + "x" * 16,
        },
        status=201,
    )
    raw = b'{"source":"acceptance"}'
    timestamp = int(time.time())
    signature = hmac.new(
        ("product-acceptance-signature-" + "x" * 16).encode(),
        str(timestamp).encode() + b".product-event." + raw,
        hashlib.sha256,
    ).hexdigest()
    endpoint = f"/api/v1/orchestration/triggers/{webhook['id']}/webhook"
    headers = {
        "X-OpsMesh-Timestamp": str(timestamp),
        "X-OpsMesh-Event-ID": "product-event",
        "X-OpsMesh-Signature": signature,
        "Content-Type": "application/json",
    }
    first_hook = await api.post(endpoint, content=raw, headers=headers)
    assert first_hook.status_code == 202, first_hook.text
    duplicate = await api.post(endpoint, content=raw, headers=headers)
    assert duplicate.json() == first_hook.json()
    invalid = await api.post(
        endpoint, content=raw, headers={**headers, "X-OpsMesh-Signature": "0" * 64}
    )
    assert invalid.status_code == 403
    calendar = await call(
        "POST",
        "/orchestration/triggers",
        {
            "revision_id": simple["id"],
            "kind": "calendar",
            "configuration": {"interval_seconds": 10},
        },
        status=201,
    )
    from opsmesh.orchestration.triggers.models import TriggerReceipt

    async def receipt(trigger):
        with database_sessions() as session:
            return session.scalar(
                select(TriggerReceipt).where(TriggerReceipt.trigger_id == UUID(trigger["id"]))
            )

    await eventually(
        lambda: receipt(calendar), lambda item: item and item.status == "accepted", seconds=45
    )
    await eventually(
        lambda: receipt(event_trigger), lambda item: item and item.status == "accepted"
    )
    await call(
        "PUT",
        f"/orchestration/triggers/{calendar['id']}/status",
        {"expected_revision": calendar["revision"], "status": "disabled"},
    )

    # Closing a use configuration cannot stop an unrelated user environment.
    other_environment = await call(
        "POST",
        "/orchestration/environments",
        {
            "image": installation["artifact"],
        },
        status=202,
        params={"idempotency_key": "other-project-environment"},
    )
    other_eid = other_environment["environment_id"]
    await eventually(
        lambda: call("GET", f"/orchestration/environments/{other_eid}"),
        lambda item: item["status"] == "ready",
    )
    with database_sessions() as session:
        state = secret_cipher(Settings()).decrypt_payload(
            session.get(Environment, UUID(other_eid)).encrypted_state
        )
        containers.append(state["container_id"])
    # A flow configuration owns its waiting FlowRun, independently of the Agent configuration.
    waiting = await publish(
        "Archive waiting child",
        [{"key": "wait", "kind": "wait", "wait_kind": "signal", "signal_name": "never"}],
    )
    waiting_configuration = await call(
        "POST",
        "/orchestration/configurations",
        {"name": "Waiting flow", "definition_revision_id": waiting["id"], "specification": {}},
        status=201,
        params={"idempotency_key": "waiting-configuration"},
    )
    waiting_base = f"/orchestration/configurations/{waiting_configuration['id']}"
    waiting_run = await call(
        "POST",
        "/orchestration/flows",
        {"revision_id": waiting["id"], "configuration_id": waiting_configuration["id"]},
        status=202,
        params={"idempotency_key": "archive-flow"},
    )
    await eventually(
        lambda: call("GET", f"/orchestration/flows/{waiting_run['id']}/steps"),
        lambda rows: rows and rows[0]["status"] == "waiting",
    )
    waiting_direct = None
    if os.environ.get("OPSMESH_TEST_MODEL_API_KEY"):
        waiting_direct = await call(
            "POST",
            base + "/runs",
            {"configuration_revision": 2, "input": "PROJECT_OK"},
            status=202,
            params={"idempotency_key": "archive-direct-run"},
        )
        waiting_direct = await eventually(
            lambda: call("GET", f"/orchestration/runs/{waiting_direct['run_id']}"),
            lambda item: item["status"]
            in {"waiting_approval", "succeeded", "failed", "needs_attention"},
        )
        assert waiting_direct["status"] == "waiting_approval", waiting_direct
    await call("POST", base + "/close", {"expected_revision": 2}, status=202)
    await call("POST", waiting_base + "/close", {"expected_revision": 1}, status=202)
    await call(
        "POST",
        base + "/runs",
        {"configuration_revision": 2, "input": "forbidden"},
        status=409,
        params={"idempotency_key": "archived-run"},
    )
    archive = await eventually(
        lambda: call("GET", base + "/close"),
        lambda item: item["status"] in {"completed", "needs_attention"},
    )
    assert archive["status"] == "completed", archive
    await eventually(
        lambda: call("GET", waiting_base + "/close"),
        lambda item: item["status"] == "completed",
    )
    # Closed configurations reject ordinary scoped access; closure receipts remain
    # readable. Verify cleanup from authoritative facts without bypassing that policy.
    await call("GET", f"/orchestration/environments/{eid}", status=409)
    await call("GET", f"/orchestration/mcp-connections/{mcp_id}", status=409)
    with database_sessions() as session:
        assert session.get(Environment, UUID(eid)).status == "closed"
        assert session.get(Environment, UUID(mcp_eid)).status == "closed"
        assert session.get(MCPConnection, UUID(mcp_id)).status == "closed"
        assert session.get(FlowRun, UUID(waiting_run["id"])).status == "cancelled"
        if waiting_direct:
            assert session.get(Run, UUID(waiting_direct["run_id"])).status == "cancelled"
    assert (await call("GET", f"/orchestration/environments/{other_eid}"))["status"] == "ready"
    await call(
        "POST",
        f"/orchestration/environments/{other_eid}/close",
        status=202,
        params={"operation_id": "close-unrelated-environment"},
    )
    await eventually(
        lambda: call("GET", f"/orchestration/environments/{other_eid}"),
        lambda item: item["status"] == "closed",
    )
