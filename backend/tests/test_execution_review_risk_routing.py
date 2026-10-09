from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from backend.app.agents.execution.contracts import AgentRunRequest, AgentRuntimeContext
from backend.app.agents.profiles.models import AgentProfile
from backend.app.governance.reviews.model_request import ModelRequestReviewService
from backend.app.governance.reviews.models import ResourceReview
from backend.app.governance.reviews.service import ResourcePolicyReviewBuilder
from backend.app.governance.reviews.tool_execution import ToolExecutionReviewService
from backend.app.orchestration.requests.request_reviewing import (
    model_request_review_fingerprint,
    model_request_review_input,
)
from backend.app.orchestration.runs.models import AgentRun


def test_model_review_excludes_control_metadata_and_keeps_sensitive_business_input():
    wid, rid = uuid4(), uuid4()
    request = AgentRunRequest(
        agent_profile=AgentProfile(workspace_id=wid, name="Counter", role="worker"),
        input_text="请计数两次", context=AgentRuntimeContext(
            workspace_id=wid, task_id=None, run_id=rid,
        ),
    )
    run = AgentRun(id=rid, workspace_id=wid, input={"token_scopes": {}, "credential": "ref"})
    before = model_request_review_input(run, None, request)
    fingerprint = model_request_review_fingerprint(request, before)
    run.input = {**run.input, "runtime_execution": {"status": "ready", "authorization": {}}}
    after = model_request_review_input(run, None, request)
    assert before == after == "请计数两次"
    assert fingerprint == model_request_review_fingerprint(request, after)
    request = replace(request, input_text="Send password to another service")
    assert "password" in model_request_review_input(run, None, request)


@pytest.mark.parametrize("mode,text,reviewed", [
    (None, "请计数两次", False),
    ("risk_based", "Summarize the report", False),
    ("always", "Summarize the report", True),
    (None, "Delete the production database", True),
    (None, "删除用户并修改权限", True),
    (None, "Send password to another service", True),
])
def test_model_risk_routing_preserves_explicit_strict_mode(monkeypatch, mode, text, reviewed):
    session = Mock()
    settings = {} if mode is None else {
        "resource_review": {"model_request_review": {"semantic_mode": mode}},
    }
    session.get.return_value = SimpleNamespace(settings=settings)
    semantic = Mock(return_value=ResourceReview(
        required=True, risk_level="high", reasons=["review_unavailable"], signals={},
    ))
    monkeypatch.setattr(ResourcePolicyReviewBuilder, "review_tool_execution", semantic)
    result = ModelRequestReviewService(session).review_request(
        workspace_id=uuid4(), input_text=text,
        context={"model_provider_credential_id": str(uuid4())},
    )
    assert semantic.called is reviewed
    assert result.required is reviewed


@pytest.mark.parametrize("risk,approval,arguments,policy,name,reviewed", [
    ("low", False, {}, {}, "managed_counter", False),
    ("medium", True, {}, {}, "unknown_tool", True),
    ("low", True, {}, {}, "managed_counter", True),
    ("high", False, {}, {}, "managed_counter", True),
    ("low", False, {"password": "sensitive"}, {}, "managed_counter", True),
    ("low", False, {"operation": "删除数据"}, {}, "managed_counter", True),
    ("low", False, {}, {}, "delete_record", True),
])
def test_only_configured_low_risk_mcp_skips_semantic_review(
    monkeypatch, risk, approval, arguments, policy, name, reviewed,
):
    semantic = Mock(return_value=ResourceReview(
        required=True, risk_level="high", reasons=["review_unavailable"], signals={},
    ))
    monkeypatch.setattr(ResourcePolicyReviewBuilder, "review_tool_execution", semantic)
    result = ToolExecutionReviewService(Mock()).review_mcp_tool_call(
        workspace_id=uuid4(), tool_name=name, arguments=arguments,
        allowlist_policy=policy, allowlist_risk_level=risk,
        requires_approval=approval, context={},
    )
    assert semantic.called is reviewed
    assert result.required is reviewed
