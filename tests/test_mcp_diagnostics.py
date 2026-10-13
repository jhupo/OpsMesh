"""Operator diagnostics remain durable, tenant scoped, redacted and expirable."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from opsmesh.capabilities.mcp.execution.contracts import (
    MCPExecutionRequest,
    MCPToolCallDiagnosticResponse,
    MCPToolCallLogResponse,
)
from opsmesh.capabilities.mcp.execution.diagnostics import normalize_failure
from opsmesh.capabilities.mcp.execution.events import (
    MCPToolCallLogQueryService,
    MCPToolCallRecorder,
)
from opsmesh.capabilities.mcp.execution.retention import expire_mcp_diagnostics
from opsmesh.capabilities.mcp.models import MCPToolCallLog
from opsmesh.shared.pagination import PageParams
from tests.fixtures.agent_product import _seed_workspace, _session
from tests.fixtures.mcp_product import seed_mcp_run


def test_diagnostics_are_durable_scoped_and_expire_without_losing_failure():
    session = _session()
    _, workspace = _seed_workspace(session, with_default_provider=False)
    run, server = seed_mcp_run(session, workspace)
    error = normalize_failure(
        {
            "exception_type": "ReadTimeout",
            "http_status": 503,
            "workspace_id": "forged",
            "password": "diagnostic-canary",
            "diagnostic": {
                "exceptions": [
                    {
                        "exception_type": "ReadTimeout",
                        "frames": [{"location_sha256": "a" * 64, "line": 12}],
                    }
                ]
            },
        },
        code="mcp_remote_error",
        message="Tool failed",
    )
    assert "diagnostic-canary" not in json.dumps(error) and "workspace_id" not in error
    request = MCPExecutionRequest(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        mcp_server_id=server.id,
        tool_name="generate_image",
        arguments={},
        tool_call_id="once",
    )
    log = MCPToolCallRecorder(session).record(
        request=request,
        server_id=server.id,
        status="failed",
        response=None,
        error=error,
        snapshot=run.input["authorization_snapshot"],
        run=run,
    )
    session.commit()
    assert "diagnostic" not in MCPToolCallLogResponse.model_validate(log).model_dump()["error"]
    assert "diagnostic" in MCPToolCallDiagnosticResponse.model_validate(log).model_dump()["error"]
    query = MCPToolCallLogQueryService(session)
    assert query.list_mcp_tool_call_logs(workspace.id, PageParams(), error_id=log.id)[1] == 1
    assert query.list_mcp_tool_call_logs(uuid4(), PageParams(), error_id=log.id)[1] == 0
    with pytest.raises(ValueError, match="not found"):
        query.get_diagnostic(uuid4(), log.id)
    log.created_at = datetime.now(UTC) - timedelta(days=8)
    session.commit()
    assert expire_mcp_diagnostics(session, days=7) == 1
    session.commit()
    assert expire_mcp_diagnostics(session, days=7) == 0
    session.refresh(log)
    assert (
        log.status == "failed"
        and log.error["http_status"] == 503
        and log.error["diagnostic_expired"] is True
    )
    assert session.scalar(select(MCPToolCallLog)) is not None
