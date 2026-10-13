from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceReviewResult:
    required: bool
    risk_level: str
    reasons: list[str]
    signals: dict[str, object]
