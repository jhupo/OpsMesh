from __future__ import annotations

from backend.app.reviews.llm import LlmReviewResult
from backend.app.reviews.models import ResourceReview
from backend.app.reviews.utils import (
    _HIGH_RISK_LEVELS,
    _max_risk,
    _normalize_risk,
)


def merge_policy_and_llm_reviews(
    policy_review: ResourceReview,
    llm_review: LlmReviewResult,
) -> ResourceReview:
    llm_required = bool(llm_review.required)
    llm_risk = _normalize_risk(llm_review.risk_level)
    llm_reasons = list(llm_review.reasons)
    llm_signals = dict(llm_review.signals)
    required = llm_required or llm_risk in _HIGH_RISK_LEVELS
    reasons = [*llm_reasons, *policy_review.reasons]
    deduped_reasons = list(dict.fromkeys(reasons))[:12]
    signals = {
        **llm_signals,
        "policy_guardrail": {
            "risk_level": policy_review.risk_level,
            "required": policy_review.required,
            "reasons": policy_review.reasons,
            "signals": policy_review.signals,
        },
    }
    return ResourceReview(
        required=required,
        risk_level=llm_risk,
        reasons=deduped_reasons or ["llm_review.approved"],
        signals=signals,
    )


def llm_unavailable_review(
    policy_review: ResourceReview,
    exc: Exception,
) -> ResourceReview:
    signals = dict(policy_review.signals)
    signals["reviewer"] = "llm_unavailable_fail_closed"
    signals["semantic_review_error"] = type(exc).__name__
    return ResourceReview(
        required=True,
        risk_level=_max_risk(policy_review.risk_level, "high"),
        reasons=list(
            dict.fromkeys(["llm_review.unavailable_requires_admin", *policy_review.reasons])
        )[:12],
        signals=signals,
    )


def review_skipped(
    policy_review: ResourceReview,
    *,
    resource_type: str,
    visibility: str,
) -> ResourceReview:
    signals = dict(policy_review.signals)
    signals["reviewer"] = "resource_review_policy"
    signals["policy_guardrail"] = {
        "risk_level": policy_review.risk_level,
        "required": policy_review.required,
        "reasons": policy_review.reasons,
        "signals": policy_review.signals,
    }
    signals["skipped_reason"] = "resource_review.disabled_for_scope"
    signals["resource_type"] = resource_type
    signals["visibility"] = visibility
    return ResourceReview(
        required=False,
        risk_level="low",
        reasons=["resource_review.disabled_for_scope"],
        signals=signals,
    )
