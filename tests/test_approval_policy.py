from unittest.mock import Mock
from uuid import uuid4

import pytest

from opsmesh.governance.policies.risky_values import normalize_risky_execution_policy_value
from opsmesh.governance.reviews.tool_execution import ToolExecutionReviewService
from opsmesh.orchestration.approvals.policy import (
    ApprovalPolicyEngine,
    ApprovalPolicyInput,
    ApprovalPolicyOutcome,
)


def test_unrelated_platform_update_preserves_configured_block_mode():
    value = normalize_risky_execution_policy_value(
        {"high_risk_tool_mode": "block", "allow_runtime_commands": True},
        {"allow_runtime_commands": False},
    )
    assert value["high_risk_tool_mode"] == "block"
    assert value["allow_runtime_commands"] is False


def test_identical_policy_inputs_produce_the_same_decision_for_every_action_kind() -> None:
    action_kinds = ("product_tool", "mcp_tool", "runtime_command")
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


def test_explicit_allow_is_not_overridden_by_risk_annotation() -> None:
    decision = ApprovalPolicyEngine.resolve(
        ApprovalPolicyInput(
            action_kind="product_tool",
            risk_level="high",
            high_risk_mode="allow",
        )
    )

    assert decision.decision == ApprovalPolicyOutcome.ALLOW
    assert "policy.human_approval.required" not in decision.reasons


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
    def unavailable_review(self: ToolExecutionReviewService, **_: object) -> None:
        raise RuntimeError("provider token=secret-value")

    monkeypatch.setattr(ToolExecutionReviewService, "review_product_tool_call", unavailable_review)

    decision = ApprovalPolicyEngine(Mock()).evaluate_product_tool(
        workspace_id=uuid4(),
        tool_name="memory.create",
        arguments={},
        context={},
    )

    assert decision.decision == ApprovalPolicyOutcome.DENY
    assert decision.risk_level == "critical"
    assert decision.signals["review_error_type"] == "RuntimeError"
    assert "secret-value" not in str(decision.approval_payload())
