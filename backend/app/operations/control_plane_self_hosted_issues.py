from __future__ import annotations

from backend.app.api.schemas.operation_control_plane import (
    OperationsControlPlaneIssueResponse,
    OperationsSelfHostedMachinesResponse,
)


def append_self_hosted_machine_issues(
    issues: list[OperationsControlPlaneIssueResponse],
    self_hosted_machines: OperationsSelfHostedMachinesResponse,
) -> None:
    unavailable = (
        self_hosted_machines.degraded
        + self_hosted_machines.quarantined
        + self_hosted_machines.revoked
        + self_hosted_machines.offline
    )
    if unavailable > 0:
        issues.append(
            OperationsControlPlaneIssueResponse(
                severity="warning",
                code="self_hosted_machines_unhealthy",
                message="Self-hosted machines need operator attention.",
                count=unavailable,
                metadata={
                    "degraded": self_hosted_machines.degraded,
                    "quarantined": self_hosted_machines.quarantined,
                    "revoked": self_hosted_machines.revoked,
                    "offline": self_hosted_machines.offline,
                },
            )
        )
    if self_hosted_machines.stale > 0:
        issues.append(
            OperationsControlPlaneIssueResponse(
                severity="warning",
                code="self_hosted_heartbeat_stale",
                message="Some self-hosted machines have stale heartbeats.",
                count=self_hosted_machines.stale,
            )
        )
