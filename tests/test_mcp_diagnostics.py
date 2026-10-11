"""Diagnostics cross SDK, Runtime and tenant-scoped persistence without secret leakage."""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.capabilities.mcp.execution.contracts import (
    McpExecutionRequest,
    McpToolCallDiagnosticResponse,
    McpToolCallLogResponse,
)
from opsmesh.capabilities.mcp.execution.diagnostics import DIAGNOSTIC_META_KEY, normalize_failure
from opsmesh.capabilities.mcp.execution.events import McpToolCallLogQueryService
from opsmesh.capabilities.mcp.execution.retention import expire_mcp_diagnostics
from opsmesh.capabilities.mcp.execution.service import McpToolExecutionService
from opsmesh.capabilities.mcp.models import McpToolCallLog
from opsmesh.capabilities.mcp.observability_routes import router
from opsmesh.identity.auth.dependencies import get_current_user
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.identity.users.models import User
from opsmesh.orchestration.runs.models import RunEvent
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.self_hosted.contracts import AuthenticatedWorker
from opsmesh.runtime.self_hosted.dispatch.mcp import SelfHostedMcpJobService
from opsmesh.runtime.self_hosted.models import RuntimeCredential, SelfHostedMcpJob, SelfHostedWorker
from opsmesh.shared.db.base import Base
from opsmesh.shared.db.session import get_db_session
from opsmesh.shared.pagination import PageParams
from opsmesh.workspaces.members.models import WorkspaceMember
from runtime.opsmesh_runtime.mcp_sdk import RuntimeMCPServerStdio
from runtime.opsmesh_runtime.stderr_capture import StderrCapture
from tests.test_mcp_execution import (
    RecordingAdapter,
    _execute_mcp,
    _seed_run_with_mcp_tool,
    _seed_workspace,
)
from tests.test_postgres_scheduler_concurrency import _temporary_postgres_schema


def test_diagnostic_validation_rejects_forged_identity_and_secret_payloads():
    result = normalize_failure(
        {
            "http_status": 503,
            "exception_type": "HTTPStatusError",
            "workspace_id": "forged",
            "message": "diagnostic-canary",
            "headers": {"secret": "diagnostic-canary"},
        },
        code="mcp_remote_error",
        message="Tool failed",
    )
    assert result["http_status"] == 503
    assert "diagnostic-canary" not in json.dumps(result)
    assert "workspace_id" not in result
    assert (
        normalize_failure({"category": ["malformed"]}, code="x", message="x")["category"]
        == "protocol"
    )


def test_stderr_capture_bounds_and_drops_untrusted_text():
    capture = StderrCapture()
    for _ in range(100):
        capture.record(b'{"exception_type":"ReadTimeout", "password":"diagnostic-canary"}')
    capture.record(b"diagnostic-canary")
    result = capture.snapshot()
    assert len(result["records"]) == 32
    assert result["records_dropped"] == 69
    assert "diagnostic-canary" not in json.dumps(result)


def test_native_sdk_notifications_and_stderr_are_private_and_bounded():
    async def exercise():
        async with RuntimeMCPServerStdio(
            params={
                "command": sys.executable,
                "args": [str(Path(__file__).parent / "fixtures/mcp_diagnostic_server.py")],
            },
            max_retry_attempts=0,
        ) as server:
            response = await server.call_tool("fail", {}, meta={"tool_call_id": "test-call"})
            await asyncio.sleep(0.1)
            result = server.diagnostic_result(response.model_dump(mode="json", by_alias=True))
            diagnostic = result["_meta"][DIAGNOSTIC_META_KEY]
            assert diagnostic["http_status"] == 503
            assert diagnostic["phase"] == "business_request"
            assert diagnostic["diagnostic"]["stderr"]["records_dropped"] >= 20
            assert len(diagnostic["diagnostic"]["stderr"]["records"]) == 32
            assert "diagnostic-canary" not in json.dumps(result)
            assert "forged" not in json.dumps(result)

    asyncio.run(exercise())


@pytest.mark.skipif(not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="Real PostgreSQL required")
def test_diagnostics_are_durable_scoped_and_expire_without_losing_failure():
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            owner, workspace = _seed_workspace(session)
            _, other = _seed_workspace(session, email="other@diagnostic.test", slug="other")
            run, server = _seed_run_with_mcp_tool(session, workspace)
            response = {
                "isError": True,
                "content": [],
                "_meta": {
                    DIAGNOSTIC_META_KEY: {
                        "category": "network",
                        "phase": "login",
                        "exception_type": "ReadTimeout",
                        "retryable": True,
                        "diagnostic": {
                            "exceptions": [
                                {
                                    "exception_type": "ReadTimeout",
                                    "frames": [{"location_sha256": "a" * 64, "line": 12}],
                                }
                            ]
                        },
                        "password": "diagnostic-canary",
                        "workspace_id": str(other.id),
                    }
                },
            }
            result = asyncio.run(
                _execute_mcp(
                    McpToolExecutionService(session, RecordingAdapter(response)),
                    McpExecutionRequest(
                        workspace_id=workspace.id,
                        agent_run_id=run.id,
                        mcp_server_id=server.id,
                        tool_name="generate_image",
                        arguments={},
                        tool_call_id="call-1",
                    ),
                )
            )
            session.commit()
            log = session.scalar(select(McpToolCallLog))
            assert result.error["error_id"] == str(log.id)
            assert "diagnostic" not in result.error
            assert "diagnostic" in log.error
            assert "diagnostic-canary" not in json.dumps(log.error)
            assert (
                "diagnostic" not in McpToolCallLogResponse.model_validate(log).model_dump()["error"]
            )
            assert (
                "diagnostic"
                in McpToolCallDiagnosticResponse.model_validate(log).model_dump()["error"]
            )
            event = session.scalar(select(RunEvent).where(RunEvent.event_type == "tool.failed"))
            assert "diagnostic" not in event.event_metadata["error"]
            query = McpToolCallLogQueryService(session)
            assert (
                query.list_mcp_tool_call_logs(
                    workspace.id,
                    PageParams(),
                    error_id=log.id,
                    agent_run_id=run.id,
                    tool_call_id="call-1",
                )[1]
                == 1
            )
            assert query.list_mcp_tool_call_logs(other.id, PageParams(), error_id=log.id)[1] == 0
            with pytest.raises(ValueError, match="not found"):
                query.get_diagnostic(other.id, log.id)
            viewer = User(email="viewer@diagnostic.test", display_name="Viewer")
            session.add(viewer)
            session.flush()
            session.add(
                WorkspaceMember(workspace_id=workspace.id, user_id=viewer.id, role="viewer")
            )
            session.commit()
            app = FastAPI()
            app.include_router(router, prefix="/api/v1")

            def database():
                with factory() as connection:
                    yield connection

            app.dependency_overrides[get_db_session] = database
            app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser.from_model(owner)
            endpoint = f"/api/v1/workspaces/{workspace.id}/capabilities/mcp-tool-call-logs"
            with TestClient(app) as client:
                normal = client.get(endpoint, params={"error_id": str(log.id)})
                assert normal.status_code == 200 and normal.json()["total"] == 1
                assert "diagnostic" not in normal.json()["items"][0]["error"]
                private = client.get(f"{endpoint}/{log.id}/diagnostic")
                assert private.status_code == 200 and "diagnostic" in private.json()["error"]
                app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser.from_model(
                    viewer
                )
                assert client.get(f"{endpoint}/{log.id}/diagnostic").status_code == 403
            log.created_at = datetime.now(UTC) - timedelta(days=8)
            session.commit()
            assert expire_mcp_diagnostics(session, days=7) == 1
            session.commit()
            assert expire_mcp_diagnostics(session, days=7) == 0
            assert log.error["exception_type"] == "ReadTimeout"
            assert log.error["diagnostic_expired"] is True
            assert log.status == "failed" and log.tool_call_id == "call-1"


@pytest.mark.skipif(not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="Real PostgreSQL required")
def test_self_hosted_completion_keeps_private_evidence_only_in_call_log():
    with _temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            _, workspace = _seed_workspace(session)
            run, server = _seed_run_with_mcp_tool(session, workspace)
            runtime = WorkspaceRuntime(workspace_id=workspace.id, name="Offline connector")
            session.add(runtime)
            session.flush()
            worker = SelfHostedWorker(
                workspace_id=workspace.id,
                workspace_runtime_id=runtime.id,
                name="Offline worker",
                machine_id="offline-worker",
            )
            session.add(worker)
            session.flush()
            job = SelfHostedMcpJob(
                workspace_id=workspace.id,
                workspace_runtime_id=runtime.id,
                worker_id=worker.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name="generate_image",
                tool_call_id="connector-call",
                status="claimed",
            )
            session.add(job)
            session.flush()
            log = McpToolCallLog(
                workspace_id=workspace.id,
                agent_run_id=run.id,
                mcp_server_id=server.id,
                tool_name=job.tool_name,
                tool_call_id=job.tool_call_id,
                status="waiting_self_hosted",
                response={"mcp_job_id": str(job.id)},
                request={},
                created_at=datetime.now(UTC),
            )
            session.add(log)
            session.commit()
            auth = AuthenticatedWorker(worker, runtime, RuntimeCredential())
            completion = SimpleNamespace(
                status="failed",
                response_payload=None,
                error_payload={
                    "code": "mcp_rpc_failed",
                    "exception_type": "ReadTimeout",
                    "diagnostic": {"exceptions": [{"exception_type": "ReadTimeout", "frames": []}]},
                    "password": "diagnostic-canary",
                },
            )
            service = SelfHostedMcpJobService(session)
            result = service.complete_mcp_job(auth, job.id, completion)
            assert "diagnostic" not in result.error_payload
            assert "diagnostic-canary" not in json.dumps(result.error_payload)
            session.refresh(log)
            assert log.error["error_id"] == str(log.id)
            assert log.error["diagnostic"]["exceptions"][0]["exception_type"] == "ReadTimeout"
            assert service.complete_mcp_job(auth, job.id, completion).id == job.id
