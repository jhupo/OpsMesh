from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.admin.policy_reader import PlatformPolicyService
from backend.app.admin.risky_policy_values import RiskyExecutionPolicy
from backend.app.core.config import Settings
from backend.app.reviews.model_request import ModelRequestReviewService
from backend.app.reviews.tool_execution import ToolExecutionReviewService


class ApprovalPolicyOutcome(StrEnum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class ApprovalPolicyInput:
    action_kind: str
    risk_level: str
    review_required: bool = False
    blocked: bool = False
    action_enabled: bool = True
    high_risk_mode: str = "require_workspace_approval"
    reasons: tuple[str, ...] = ()
    signals: dict[str, object] | None = None


@dataclass(frozen=True)
class ApprovalPolicyDecision:
    decision: ApprovalPolicyOutcome
    risk_level: str
    reasons: list[str]
    signals: dict[str, object]

    @property
    def approved(self) -> bool:
        return self.decision == ApprovalPolicyOutcome.ALLOW

    @property
    def required(self) -> bool:
        return self.decision == ApprovalPolicyOutcome.REQUIRE_APPROVAL

    @property
    def blocked(self) -> bool:
        return self.decision == ApprovalPolicyOutcome.DENY

    def approval_payload(self) -> dict[str, object]:
        return {
            "decision": self.decision.value,
            "required": self.required,
            "blocked": self.blocked,
            "risk_level": self.risk_level,
            "reasons": list(self.reasons),
            "signals": dict(self.signals),
        }


class ApprovalPolicyEngine:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings

    def evaluate_product_tool(
        self,
        *,
        workspace_id: UUID,
        tool_name: str,
        arguments: dict[str, object],
        context: dict[str, object],
    ) -> ApprovalPolicyDecision:
        try:
            review = ToolExecutionReviewService(
                self._session,
                self._settings,
            ).review_product_tool_call(
                workspace_id=workspace_id,
                tool_name=tool_name,
                arguments=arguments,
                context=context,
            )
        except Exception as exc:
            return self._review_failure("product_tool", exc)
        return self.resolve(
            ApprovalPolicyInput(
                action_kind="product_tool",
                risk_level=review.risk_level,
                review_required=review.required,
                blocked=review.blocked,
                high_risk_mode=self._risky_policy().high_risk_tool_mode,
                reasons=tuple(review.reasons),
                signals=review.signals,
            )
        )

    def evaluate_mcp_tool(
        self,
        *,
        workspace_id: UUID,
        tool_name: str,
        arguments: dict[str, object],
        allowlist_policy: dict[str, object],
        allowlist_risk_level: str,
        requires_approval: bool,
        context: dict[str, object],
    ) -> ApprovalPolicyDecision:
        policy = self._risky_policy()
        if (
            _normalize_risk(allowlist_risk_level)[0] in {"high", "critical"}
            and policy.high_risk_tool_mode == "block"
        ):
            return self.resolve(
                ApprovalPolicyInput(
                    action_kind="mcp_tool",
                    risk_level=allowlist_risk_level,
                    blocked=True,
                    high_risk_mode=policy.high_risk_tool_mode,
                    reasons=("platform.high_risk_tool.blocked",),
                )
            )
        try:
            review = ToolExecutionReviewService(
                self._session,
                self._settings,
            ).review_mcp_tool_call(
                workspace_id=workspace_id,
                tool_name=tool_name,
                arguments=arguments,
                allowlist_policy=allowlist_policy,
                allowlist_risk_level=allowlist_risk_level,
                requires_approval=requires_approval,
                context=context,
            )
        except Exception as exc:
            return self._review_failure("mcp_tool", exc)
        return self.resolve(
            ApprovalPolicyInput(
                action_kind="mcp_tool",
                risk_level=review.risk_level,
                review_required=review.required or requires_approval,
                blocked=review.blocked,
                high_risk_mode=policy.high_risk_tool_mode,
                reasons=tuple(review.reasons),
                signals=review.signals,
            )
        )

    def evaluate_model_request(
        self,
        *,
        workspace_id: UUID,
        input_text: str,
        context: dict[str, object],
    ) -> ApprovalPolicyDecision:
        try:
            review = ModelRequestReviewService(
                self._session,
                self._settings,
            ).review_request(
                workspace_id=workspace_id,
                input_text=input_text,
                context=context,
            )
        except Exception as exc:
            return self._review_failure("model_request", exc)
        return self.resolve(
            ApprovalPolicyInput(
                action_kind="model_request",
                risk_level=review.risk_level,
                review_required=review.required,
                reasons=tuple(review.reasons),
                signals=review.signals,
            )
        )

    def evaluate_runtime_command(
        self,
        *,
        workspace_id: UUID,
        command: list[str],
        context: dict[str, object],
    ) -> ApprovalPolicyDecision:
        policy = self._risky_policy()
        if not policy.allow_runtime_commands:
            return self.resolve(
                ApprovalPolicyInput(
                    action_kind="runtime_command",
                    risk_level="critical",
                    blocked=True,
                    action_enabled=False,
                    high_risk_mode=policy.high_risk_tool_mode,
                    reasons=("platform.runtime_command.disabled",),
                )
            )
        try:
            review = ToolExecutionReviewService(
                self._session,
                self._settings,
            ).review_runtime_command(
                workspace_id=workspace_id,
                command=command,
                context=context,
            )
        except Exception as exc:
            return self._review_failure("runtime_command", exc)
        return self.resolve(
            ApprovalPolicyInput(
                action_kind="runtime_command",
                risk_level=review.risk_level,
                review_required=review.required,
                blocked=review.blocked,
                high_risk_mode=policy.high_risk_tool_mode,
                reasons=tuple(review.reasons),
                signals=review.signals,
            )
        )

    @staticmethod
    def resolve(policy_input: ApprovalPolicyInput) -> ApprovalPolicyDecision:
        risk_level, valid_risk = _normalize_risk(policy_input.risk_level)
        reasons = list(policy_input.reasons)
        signals = dict(policy_input.signals or {})
        signals["approval_policy"] = {
            "action_kind": policy_input.action_kind,
            "action_enabled": policy_input.action_enabled,
            "high_risk_mode": policy_input.high_risk_mode,
        }

        outcome = ApprovalPolicyOutcome.ALLOW
        if not valid_risk:
            risk_level = "critical"
            reasons.append("policy.risk_level.invalid")
            outcome = ApprovalPolicyOutcome.DENY
        elif policy_input.high_risk_mode not in {
            "allow",
            "require_workspace_approval",
            "block",
        }:
            reasons.append("policy.high_risk_mode.invalid")
            outcome = ApprovalPolicyOutcome.DENY
        elif not policy_input.action_enabled:
            reasons.append("policy.action.disabled")
            outcome = ApprovalPolicyOutcome.DENY
        elif policy_input.blocked:
            outcome = ApprovalPolicyOutcome.DENY
        elif risk_level in {"high", "critical"} and policy_input.high_risk_mode == "block":
            reasons.append("policy.high_risk_action.blocked")
            outcome = ApprovalPolicyOutcome.DENY
        elif policy_input.review_required or risk_level in {"high", "critical"}:
            reasons.append("policy.human_approval.required")
            outcome = ApprovalPolicyOutcome.REQUIRE_APPROVAL

        return ApprovalPolicyDecision(
            decision=outcome,
            risk_level=risk_level,
            reasons=list(dict.fromkeys(reasons))[:12],
            signals=signals,
        )

    def _review_failure(self, action_kind: str, exc: Exception) -> ApprovalPolicyDecision:
        return self.resolve(
            ApprovalPolicyInput(
                action_kind=action_kind,
                risk_level="critical",
                blocked=True,
                reasons=("policy.review.unavailable",),
                signals={"review_error_type": type(exc).__name__},
            )
        )

    def _risky_policy(self) -> RiskyExecutionPolicy:
        return PlatformPolicyService(self._session).risky_execution_policy()


def _normalize_risk(value: object) -> tuple[str, bool]:
    risk = str(value or "").strip().lower()
    return (risk, True) if risk in {"low", "medium", "high", "critical"} else ("critical", False)


__all__ = [
    "ApprovalPolicyDecision",
    "ApprovalPolicyEngine",
    "ApprovalPolicyInput",
    "ApprovalPolicyOutcome",
]
