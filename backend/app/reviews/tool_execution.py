from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.reviews.service import ResourcePolicyReviewBuilder
from backend.app.security.redaction import redact_sensitive_payload

_HIGH_RISK_TERMS = {
    "authorization",
    "credential",
    "delete",
    "drop",
    "exec",
    "filesystem",
    "network",
    "password",
    "payment",
    "production",
    "secret",
    "shell",
    "sudo",
    "token",
    "write",
}
_SENSITIVE_ARGUMENT_KEYS = {
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
}


@dataclass(frozen=True)
class ToolExecutionReview:
    required: bool
    blocked: bool
    risk_level: str
    reasons: list[str]
    signals: dict[str, object]

    @property
    def approved(self) -> bool:
        return not self.required and not self.blocked

    def approval_payload(self) -> dict[str, object]:
        return {
            "required": self.required,
            "blocked": self.blocked,
            "risk_level": self.risk_level,
            "reasons": list(self.reasons),
            "signals": dict(self.signals),
        }


class ToolExecutionReviewService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._resource_reviews = ResourcePolicyReviewBuilder(session, settings)

    def review_mcp_tool_call(
        self,
        *,
        workspace_id: UUID,
        tool_name: str,
        arguments: dict[str, object],
        allowlist_policy: dict[str, object],
        allowlist_risk_level: str,
        requires_approval: bool,
        context: dict[str, object],
    ) -> ToolExecutionReview:
        static = _static_tool_review(
            tool_kind="mcp",
            tool_name=tool_name,
            arguments=arguments,
            allowlist_policy=allowlist_policy,
            allowlist_risk_level=allowlist_risk_level,
            requires_approval=requires_approval,
            context=context,
        )
        semantic = self._resource_reviews.review_tool_execution(
            workspace_id=workspace_id,
            tool_kind="mcp",
            tool_name=tool_name,
            arguments=arguments,
            static_signals=static.signals,
            context=context,
        )
        return _merge_reviews(static, semantic, arguments)

    def review_product_tool_call(
        self,
        *,
        workspace_id: UUID,
        tool_name: str,
        arguments: dict[str, object],
        context: dict[str, object],
    ) -> ToolExecutionReview:
        static = _static_tool_review(
            tool_kind="product",
            tool_name=tool_name,
            arguments=arguments,
            allowlist_policy={},
            allowlist_risk_level=_product_tool_risk(tool_name),
            requires_approval=False,
            context=context,
        )
        semantic = self._resource_reviews.review_tool_execution(
            workspace_id=workspace_id,
            tool_kind="product",
            tool_name=tool_name,
            arguments=arguments,
            static_signals=static.signals,
            context=context,
        )
        return _merge_reviews(static, semantic, arguments)

    def review_runtime_command(
        self,
        *,
        workspace_id: UUID,
        command: list[str],
        context: dict[str, object],
    ) -> ToolExecutionReview:
        arguments: dict[str, object] = {"command": command}
        static = _static_tool_review(
            tool_kind="runtime_command",
            tool_name="runtime_shell",
            arguments=arguments,
            allowlist_policy={},
            allowlist_risk_level=_runtime_command_risk(command),
            requires_approval=False,
            context=context,
        )
        semantic = self._resource_reviews.review_tool_execution(
            workspace_id=workspace_id,
            tool_kind="runtime_command",
            tool_name="runtime_shell",
            arguments=arguments,
            static_signals=static.signals,
            context=context,
        )
        return _merge_reviews(static, semantic, arguments)


def _merge_reviews(
    static: ToolExecutionReview,
    semantic: object,
    arguments: dict[str, object],
) -> ToolExecutionReview:
    risk_level = _max_risk(static.risk_level, getattr(semantic, "risk_level", "medium"))
    required = static.required or bool(getattr(semantic, "required", False))
    required = required or risk_level in {"high", "critical"}
    reasons = list(
        dict.fromkeys(
            [
                *list(getattr(semantic, "reasons", [])),
                *static.reasons,
            ]
        )
    )[:12]
    signals = {
        **dict(getattr(semantic, "signals", {})),
        "static_tool_review": static.signals,
        "arguments_preview": redact_sensitive_payload(arguments),
    }
    return ToolExecutionReview(
        required=required,
        blocked=static.blocked,
        risk_level=risk_level,
        reasons=reasons,
        signals=signals,
    )


def _static_tool_review(
    *,
    tool_kind: str,
    tool_name: str,
    arguments: dict[str, object],
    allowlist_policy: dict[str, object],
    allowlist_risk_level: str,
    requires_approval: bool,
    context: dict[str, object],
) -> ToolExecutionReview:
    reasons: list[str] = []
    risk_level = _normalize_risk(allowlist_risk_level)
    if requires_approval:
        reasons.append("tool.policy.requires_approval")
        risk_level = _max_risk(risk_level, "medium")
    if risk_level in {"high", "critical"}:
        reasons.append(f"tool.policy.risk_level.{risk_level}")
    matched_terms = _matched_terms(arguments)
    if matched_terms:
        reasons.append("tool.arguments.contains_sensitive_or_dangerous_terms")
        risk_level = _max_risk(risk_level, "high")
    if _has_sensitive_argument_key(arguments):
        reasons.append("tool.arguments.contains_sensitive_keys")
        risk_level = _max_risk(risk_level, "high")
    if _policy_requires_admin_review(allowlist_policy):
        reasons.append("tool.policy.requires_admin_review")
        risk_level = _max_risk(risk_level, "high")
    signals = {
        "signal_source": "tool_execution_policy_signals",
        "tool_kind": tool_kind,
        "tool_name": tool_name,
        "matched_terms": matched_terms,
        "policy": redact_sensitive_payload(allowlist_policy),
        "context": redact_sensitive_payload(context),
    }
    if not reasons:
        reasons.append("tool.low_risk")
    return ToolExecutionReview(
        required=bool(matched_terms) or risk_level == "critical" or requires_approval,
        blocked=False,
        risk_level=risk_level,
        reasons=reasons,
        signals=signals,
    )


def _matched_terms(arguments: dict[str, object]) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    _scan_value(arguments, "arguments", matches)
    return matches[:20]


def _scan_value(value: object, path: str, matches: list[dict[str, str]]) -> None:
    if isinstance(value, str):
        lowered = value.lower()
        for term in sorted(_HIGH_RISK_TERMS):
            if term in lowered:
                matches.append({"path": path, "term": term})
        return
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if isinstance(key, str) else path
            _scan_value(item, child_path, matches)
        return
    if isinstance(value, list):
        for index, item in enumerate(value[:50]):
            _scan_value(item, f"{path}[{index}]", matches)


def _has_sensitive_argument_key(arguments: dict[str, object]) -> bool:
    for key, value in arguments.items():
        if isinstance(key, str) and any(term in key.lower() for term in _SENSITIVE_ARGUMENT_KEYS):
            return True
        if isinstance(value, dict) and _has_sensitive_argument_key(value):
            return True
    return False


def _policy_requires_admin_review(policy: dict[str, object]) -> bool:
    review = policy.get("execution_review")
    if not isinstance(review, dict):
        return False
    return review.get("require_admin_review") is True


def _product_tool_risk(tool_name: str) -> str:
    if tool_name in {
        "archive_workspace_memory",
        "mark_agent_message_read",
        "promote_working_memory",
        "remember_workspace_memory",
        "send_agent_message",
        "write_artifact",
    }:
        return "medium"
    if tool_name in {"read_workspace_file", "search_workspace_memory"}:
        return "medium"
    return "low"


def _runtime_command_risk(command: list[str]) -> str:
    lowered = [item.lower() for item in command if isinstance(item, str)]
    if any(token in lowered for token in {"rm", "mkfs", "shutdown", "reboot", "dd"}):
        return "high"
    if any(
        item in {"sh", "bash", "powershell", "curl", "wget", "ssh", "scp"}
        for item in lowered
    ):
        return "high"
    if any(symbol in lowered for symbol in {">", ">>", "|", "&&", "||", ";"}):
        return "high"
    return "medium" if command else "high"


def _normalize_risk(value: object) -> str:
    risk = str(value or "low").lower().strip()
    return risk if risk in {"low", "medium", "high", "critical"} else "medium"


def _max_risk(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    left_risk = _normalize_risk(left)
    right_risk = _normalize_risk(right)
    return left_risk if order[left_risk] >= order[right_risk] else right_risk
