from __future__ import annotations

from backend.app.reviews.models import ResourceReview
from backend.app.reviews.utils import (
    _DANGEROUS_WORDS,
    _HIGH_RISK_LEVELS,
    _HIGH_RISK_TERMS,
    _REVIEW_RISK_ORDER,
    _normalize_risk,
    _path_has_dangerous_term,
    _walk_mapping,
)


class ReviewScanner:
    def __init__(self) -> None:
        self._risk_level = "low"
        self._reasons: list[str] = []
        self._signals: dict[str, object] = {"matched_terms": []}

    def add(self, risk_level: str, reason: str) -> None:
        normalized = _normalize_risk(risk_level)
        if _REVIEW_RISK_ORDER[normalized] > _REVIEW_RISK_ORDER[self._risk_level]:
            self._risk_level = normalized
        if reason not in self._reasons:
            self._reasons.append(reason)

    def scan_text(self, field: str, value: object) -> None:
        if not isinstance(value, str) or not value:
            return
        lowered = value.lower()
        matches = sorted(term for term in _DANGEROUS_WORDS if term in lowered)
        if not matches:
            return
        matched_terms = self._signals.setdefault("matched_terms", [])
        if isinstance(matched_terms, list):
            for match in matches:
                entry = {"field": field, "term": match}
                if entry not in matched_terms:
                    matched_terms.append(entry)
        risk_level = (
            "medium"
            if field.startswith(("connection.", "manifest.", "policy."))
            else "high"
            if any(match in _HIGH_RISK_TERMS for match in matches)
            else "medium"
        )
        self.add(risk_level, f"{field}.contains_sensitive_terms")

    def scan_mapping(self, field: str, value: object) -> None:
        if not isinstance(value, dict):
            return
        for path, item in _walk_mapping(value, field):
            if isinstance(item, str):
                self.scan_text(path, item)
            elif isinstance(item, bool) and item and _path_has_dangerous_term(path):
                self.add("medium", f"{path}.enabled")

    def result(self, *, reviewer: str) -> ResourceReview:
        reasons = list(self._reasons)
        required = self._risk_level in _HIGH_RISK_LEVELS
        if not reasons:
            reasons.append("resource.low_risk")
        signals = dict(self._signals)
        signals["reviewer"] = reviewer
        return ResourceReview(
            required=required,
            risk_level=self._risk_level,
            reasons=reasons,
            signals=signals,
        )
