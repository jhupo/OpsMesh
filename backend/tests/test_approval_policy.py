from unittest.mock import Mock
from uuid import uuid4

import pytest

from backend.app.agent_runtime.contracts import AgentRunRequest, AgentRuntimeContext
from backend.app.agents.models import AgentProfile
from backend.app.approvals.models import Approval
from backend.app.approvals.policy import (
    ApprovalPolicyDecision,
    ApprovalPolicyEngine,
    ApprovalPolicyInput,
    ApprovalPolicyOutcome,
)
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.orchestration.model_request_approval import ModelRequestApprovalService
from backend.app.reviews.model_request import ModelRequestReviewService
from backend.app.runs.models import AgentRun
from backend.app.runs.status import RunStatus


def test_identical_policy_inputs_produce_the_same_decision_for_every_action_kind() -> None:
    action_kinds = ("product_tool", "mcp_tool", "model_request", "runtime_command")
    scenarios = (
        ({"risk_level": "low"}, ApprovalPolicyOutcome.ALLOW),
        (
            {"risk_level": "medium", "review_required": True},
            ApprovalPolicyOutcome.REQUIRE_APPROVAL,
        ),
        ({"risk_level": "medium", "blocked": True}, ApprovalPolicyOutcome.DENY),
    )

    for values, expected in scenarios:
        decisions = {
            ApprovalPolicyEngine.resolve(
                ApprovalPolicyInput(action_kind=action_kind, **values)
            ).decision
            for action_kind in action_kinds
        }
        assert decisions == {expected}


def test_high_risk_action_cannot_be_automatically_allowed() -> None:
    decision = ApprovalPolicyEngine.resolve(
        ApprovalPolicyInput(
            action_kind="product_tool",
            risk_level="high",
            high_risk_mode="allow",
        )
    )

    assert decision.decision == ApprovalPolicyOutcome.REQUIRE_APPROVAL
    assert "policy.human_approval.required" in decision.reasons


def test_block_and_invalid_policy_inputs_fail_closed() -> None:
    blocked = ApprovalPolicyEngine.resolve(
        ApprovalPolicyInput(
            action_kind="mcp_tool",
            risk_level="high",
            high_risk_mode="block",
        )
    )
    invalid = ApprovalPolicyEngine.resolve(
        ApprovalPolicyInput(
            action_kind="runtime_command",
            risk_level="unexpected",
        )
    )

    assert blocked.decision == ApprovalPolicyOutcome.DENY
    assert invalid.decision == ApprovalPolicyOutcome.DENY
    assert invalid.risk_level == "critical"


def test_review_failure_is_denied_without_exposing_error_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_review(self: ModelRequestReviewService, **_: object) -> None:
        raise RuntimeError("provider token=secret-value")

    monkeypatch.setattr(ModelRequestReviewService, "review_request", unavailable_review)

    decision = ApprovalPolicyEngine(Mock()).evaluate_model_request(
        workspace_id=uuid4(),
        input_text="ordinary request",
        context={},
    )

    assert decision.decision == ApprovalPolicyOutcome.DENY
    assert decision.risk_level == "critical"
    assert decision.signals["review_error_type"] == "RuntimeError"
    assert "secret-value" not in str(decision.approval_payload())


def test_denied_model_request_fails_the_run_instead_of_requesting_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = ApprovalPolicyDecision(
        decision=ApprovalPolicyOutcome.DENY,
        risk_level="critical",
        reasons=["policy.review.unavailable"],
        signals={},
    )
    monkeypatch.setattr(
        ApprovalPolicyEngine,
        "evaluate_model_request",
        lambda self, **kwargs: decision,
    )
    workspace_id = uuid4()
    run_id = uuid4()
    run = AgentRun(
        id=run_id,
        workspace_id=workspace_id,
        task_id=None,
        status=RunStatus.RUNNING.value,
        input={},
    )
    profile = AgentProfile(
        id=uuid4(),
        workspace_id=workspace_id,
        name="Worker",
        role="worker",
    )
    request = AgentRunRequest(
        agent_profile=profile,
        input_text="Run the task",
        context=AgentRuntimeContext(
            workspace_id=workspace_id,
            task_id=None,
            run_id=run_id,
        ),
    )
    session = Mock()
    events = Mock()

    stopped = ModelRequestApprovalService(
        session=session,
        settings=None,
        events=events,
    ).requires_approval(run, request)

    assert stopped is True
    assert run.status == RunStatus.FAILED.value
    session.scalar.assert_not_called()
    events.append_event.assert_called_once()
    assert events.append_event.call_args.args[1] == "model.request_blocked"


def test_model_request_human_decision_creates_approval_and_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision = ApprovalPolicyDecision(
        decision=ApprovalPolicyOutcome.REQUIRE_APPROVAL,
        risk_level="high",
        reasons=["policy.human_approval.required"],
        signals={},
    )
    monkeypatch.setattr(
        ApprovalPolicyEngine,
        "evaluate_model_request",
        lambda self, **kwargs: decision,
    )
    workspace_id = uuid4()
    run_id = uuid4()
    run = AgentRun(
        id=run_id,
        workspace_id=workspace_id,
        task_id=None,
        status=RunStatus.RUNNING.value,
        input={},
    )
    request = AgentRunRequest(
        agent_profile=AgentProfile(
            id=uuid4(),
            workspace_id=workspace_id,
            name="Worker",
            role="worker",
        ),
        input_text="Run the task",
        context=AgentRuntimeContext(
            workspace_id=workspace_id,
            task_id=None,
            run_id=run_id,
        ),
    )
    session = Mock()
    session.scalar.return_value = None
    events = Mock()

    stopped = ModelRequestApprovalService(
        session=session,
        settings=None,
        events=events,
    ).requires_approval(run, request)

    approval = session.add.call_args.args[0]
    assert stopped is True
    assert isinstance(approval, Approval)
    assert approval.approval_type == "model.request"
    assert approval.payload["review"]["decision"] == "require_approval"
    assert run.status == RunStatus.WAITING_APPROVAL.value
    events.append_event.assert_called_once()
    assert events.append_event.call_args.args[1] == "approval.requested"
