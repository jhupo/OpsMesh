from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.reviews.constants import (
    MODEL_REQUEST_REVIEW_SETTINGS_KEY,
    RESOURCE_REVIEW_SETTINGS_KEY,
)
from backend.app.reviews.service import ResourceReviewService
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.workspaces.models import Workspace

_SENSITIVE_TERMS = {
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
}
_SEMANTIC_MODE_ALWAYS = "always"


@dataclass(frozen=True)
class ModelRequestReview:
    required: bool
    risk_level: str
    reasons: list[str]
    signals: dict[str, object]

    @property
    def approved(self) -> bool:
        return not self.required

    def approval_payload(self) -> dict[str, object]:
        return {
            "required": self.required,
            "risk_level": self.risk_level,
            "reasons": list(self.reasons),
            "signals": dict(self.signals),
        }


class ModelRequestReviewService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._resource_reviews = ResourceReviewService(session, settings)

    def review_request(
        self,
        *,
        workspace_id: UUID,
        input_text: str,
        context: dict[str, object],
    ) -> ModelRequestReview:
        static = _static_model_request_review(input_text=input_text, context=context)
        self._require_semantic_review_enabled(workspace_id)
        semantic = self._resource_reviews.review_tool_execution(
            workspace_id=workspace_id,
            tool_kind="model_request",
            tool_name="model.run",
            arguments={
                "input_preview": input_text[:4_000],
            },
            static_signals=static.signals,
            context=redact_sensitive_payload(context),
        )
        risk_level = _max_risk(static.risk_level, semantic.risk_level)
        required = static.required or semantic.required or risk_level in {"high", "critical"}
        reasons = list(dict.fromkeys([*semantic.reasons, *static.reasons]))[:12]
        signals = {
            **semantic.signals,
            "static_model_request_review": static.signals,
            "input_preview": redact_sensitive_payload({"input": input_text[:1_000]}),
        }
        return ModelRequestReview(
            required=required,
            risk_level=risk_level,
            reasons=reasons,
            signals=signals,
        )

    def _require_semantic_review_enabled(self, workspace_id: UUID) -> None:
        workspace = self._session.get(Workspace, workspace_id)
        settings = workspace.settings if workspace is not None else {}
        if not isinstance(settings, dict):
            return
        raw_resource_review = settings.get(RESOURCE_REVIEW_SETTINGS_KEY)
        if not isinstance(raw_resource_review, dict):
            return
        raw_model_request = raw_resource_review.get(MODEL_REQUEST_REVIEW_SETTINGS_KEY)
        if not isinstance(raw_model_request, dict):
            return
        mode = str(raw_model_request.get("semantic_mode") or _SEMANTIC_MODE_ALWAYS)
        normalized = mode.strip().lower()
        if normalized != _SEMANTIC_MODE_ALWAYS:
            raise ValueError("Model request semantic review must remain enabled")


def _static_model_request_review(
    *,
    input_text: str,
    context: dict[str, object],
) -> ModelRequestReview:
    matches = _matched_terms(input_text)
    reasons = ["model_request.low_risk"]
    risk_level = "low"
    if matches:
        reasons = ["model_request.input_contains_sensitive_terms"]
        risk_level = "high"
    return ModelRequestReview(
        required=bool(matches),
        risk_level=risk_level,
        reasons=reasons,
        signals={
            "signal_source": "model_request_policy_signals",
            "matched_terms": matches,
            "context": redact_sensitive_payload(context),
        },
    )


def _matched_terms(value: str) -> list[str]:
    lowered = value.lower()
    return sorted(term for term in _SENSITIVE_TERMS if term in lowered)


def _max_risk(left: str, right: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    left_risk = left if left in order else "medium"
    right_risk = right if right in order else "medium"
    return left_risk if order[left_risk] >= order[right_risk] else right_risk
