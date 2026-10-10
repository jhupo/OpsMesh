from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.governance.reviews.configured import ConfiguredApprovalService
from opsmesh.shared.config import Settings


@dataclass(frozen=True)
class ModelRequestReview:
    required: bool
    risk_level: str
    reasons: list[str]
    signals: dict[str, object]
    blocked: bool = False

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


class ModelRequestReviewService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._reviewer = ConfiguredApprovalService(session, settings)

    def review_request(
        self, *, workspace_id: UUID, input_text: str, context: dict[str, object]
    ) -> ModelRequestReview:
        review = self._reviewer.review(
            workspace_id=workspace_id,
            action="model_request",
            name="model.run",
            arguments={"input": input_text},
            context=context,
        )
        return ModelRequestReview(
            review.required, review.risk_level, review.reasons, review.signals, review.blocked
        )
