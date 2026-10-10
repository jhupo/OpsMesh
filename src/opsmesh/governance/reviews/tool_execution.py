from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.governance.reviews.configured import ConfiguredApprovalService, ConfiguredReview
from opsmesh.shared.config import Settings


@dataclass(frozen=True)
class ToolExecutionReview(ConfiguredReview):
    @property
    def approved(self) -> bool:
        return not self.required and not self.blocked

    def approval_payload(self) -> dict[str, object]:
        return dict(
            required=self.required,
            blocked=self.blocked,
            risk_level=self.risk_level,
            reasons=self.reasons,
            signals=self.signals,
        )


class ToolExecutionReviewService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._reviewer = ConfiguredApprovalService(session, settings)

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
        policy = allowlist_policy.get("execution_review")
        explicit = isinstance(policy, dict) and policy.get("require_admin_review") is True
        return self._convert(
            self._reviewer.review(
                workspace_id=workspace_id,
                action="mcp_tool",
                name=tool_name,
                arguments=arguments,
                context=context,
                configured_review=requires_approval or explicit,
                force_human=explicit,
            )
        )

    def review_product_tool_call(
        self,
        *,
        workspace_id: UUID,
        tool_name: str,
        arguments: dict[str, object],
        context: dict[str, object],
    ) -> ToolExecutionReview:
        return self._convert(
            self._reviewer.review(
                workspace_id=workspace_id,
                action="product_tool",
                name=tool_name,
                arguments=arguments,
                context=context,
            )
        )

    def review_runtime_command(
        self, *, workspace_id: UUID, command: list[str], context: dict[str, object]
    ) -> ToolExecutionReview:
        return self._convert(
            self._reviewer.review(
                workspace_id=workspace_id,
                action="runtime_command",
                name=command[0] if command else "",
                arguments={"command": command},
                context=context,
            )
        )

    @staticmethod
    def _convert(review: ConfiguredReview) -> ToolExecutionReview:
        return ToolExecutionReview(
            review.required, review.blocked, review.risk_level, review.reasons, review.signals
        )
