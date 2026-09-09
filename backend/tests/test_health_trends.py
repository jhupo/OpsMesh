from backend.app.workspaces.health_trends import risk_changes
from backend.app.workspaces.models import WorkspaceHealthSnapshot


def test_health_risk_trends_distinguish_resolved_new_improved_and_worsened() -> None:
    previous = WorkspaceHealthSnapshot(
        risk_items=[
            {"code": "resolved", "count": 2},
            {"code": "improved", "count": 3},
            {"code": "worsened", "count": 1},
        ]
    )
    latest = WorkspaceHealthSnapshot(
        risk_items=[
            {"code": "new", "count": 1},
            {"code": "improved", "count": 1},
            {"code": "worsened", "count": 3},
        ]
    )
    result = risk_changes(latest, previous)
    for category in ("resolved", "new", "improved", "worsened"):
        assert [item["code"] for item in result[category]] == [category]
