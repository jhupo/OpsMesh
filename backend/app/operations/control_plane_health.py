from __future__ import annotations

from backend.app.api.schemas.operation_control_plane import OperationsControlPlaneIssueResponse


def control_plane_health(issues: list[OperationsControlPlaneIssueResponse]) -> str:
    severities = {issue.severity for issue in issues}
    if "critical" in severities:
        return "critical"
    if "warning" in severities:
        return "warning"
    return "healthy"
