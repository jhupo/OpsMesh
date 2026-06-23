from __future__ import annotations

from backend.app.core.typing import int_or_zero
from backend.app.workspaces.models import WorkspaceHealthSnapshot


def snapshot_payload(snapshot: WorkspaceHealthSnapshot | None) -> dict[str, object] | None:
    if snapshot is None:
        return None
    return {
        "id": snapshot.id,
        "created_at": snapshot.created_at,
        "status": snapshot.status,
        "score": snapshot.score,
        "risk_count": len(snapshot.risk_items or []),
        "recommended_action_count": len(snapshot.recommended_actions or []),
    }


def score_delta(
    latest: WorkspaceHealthSnapshot | None,
    previous: WorkspaceHealthSnapshot | None,
) -> int | None:
    if latest is None or previous is None:
        return None
    return latest.score - previous.score


def status_change(
    latest: WorkspaceHealthSnapshot | None,
    previous: WorkspaceHealthSnapshot | None,
) -> dict[str, object] | None:
    if latest is None or previous is None:
        return None
    return {
        "from": previous.status,
        "to": latest.status,
        "changed": previous.status != latest.status,
        "direction": status_direction(previous.status, latest.status),
    }


def status_direction(previous: str, latest: str) -> str:
    rank = {"critical": 0, "degraded": 1, "healthy": 2}
    previous_rank = rank.get(previous, 1)
    latest_rank = rank.get(latest, 1)
    if latest_rank > previous_rank:
        return "improved"
    if latest_rank < previous_rank:
        return "worsened"
    return "unchanged"


def risk_changes(
    latest: WorkspaceHealthSnapshot | None,
    previous: WorkspaceHealthSnapshot | None,
) -> dict[str, object]:
    latest_risks = risk_counts(latest)
    previous_risks = risk_counts(previous)
    codes = sorted(set(latest_risks) | set(previous_risks))
    changes = [
        {
            "code": code,
            "previous_count": previous_risks.get(code, 0),
            "latest_count": latest_risks.get(code, 0),
            "delta": latest_risks.get(code, 0) - previous_risks.get(code, 0),
        }
        for code in codes
    ]
    return {
        "resolved": [
            item for item in changes if item["previous_count"] > 0 and item["latest_count"] == 0
        ],
        "new": [
            item for item in changes if item["previous_count"] == 0 and item["latest_count"] > 0
        ],
        "improved": [item for item in changes if item["delta"] < 0 and item["latest_count"] > 0],
        "worsened": [item for item in changes if item["delta"] > 0 and item["previous_count"] > 0],
        "all": changes,
    }


def risk_counts(snapshot: WorkspaceHealthSnapshot | None) -> dict[str, int]:
    if snapshot is None:
        return {}
    counts: dict[str, int] = {}
    for item in snapshot.risk_items or []:
        code = item.get("code")
        if not isinstance(code, str):
            continue
        counts[code] = int_or_zero(item.get("count"))
    return counts


def recommendation_changes(
    latest: WorkspaceHealthSnapshot | None,
    previous: WorkspaceHealthSnapshot | None,
) -> dict[str, list[str]]:
    latest_actions = set(latest.recommended_actions if latest is not None else [])
    previous_actions = set(previous.recommended_actions if previous is not None else [])
    return {
        "added": sorted(latest_actions - previous_actions),
        "removed": sorted(previous_actions - latest_actions),
        "active": sorted(latest_actions),
    }
