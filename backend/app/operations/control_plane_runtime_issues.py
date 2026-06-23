from __future__ import annotations

from backend.app.api.schemas.operation_capacity import OperationsRuntimeCapacityResponse
from backend.app.api.schemas.operation_control_plane import OperationsControlPlaneIssueResponse


def append_runtime_capacity_issues(
    issues: list[OperationsControlPlaneIssueResponse],
    runtime_capacity: OperationsRuntimeCapacityResponse,
) -> None:
    paused_spaces = [space for space in runtime_capacity.runtime_spaces if space.status == "paused"]
    if paused_spaces:
        issues.append(
            OperationsControlPlaneIssueResponse(
                severity="warning",
                code="runtime_spaces_paused",
                message="Runtime spaces are paused and will not accept new runs.",
                count=len(paused_spaces),
                metadata={
                    "runtime_space_ids": [str(space.runtime_space_id) for space in paused_spaces]
                },
            )
        )

    saturated_spaces = [space for space in runtime_capacity.runtime_spaces if space.saturated]
    if saturated_spaces:
        issues.append(
            OperationsControlPlaneIssueResponse(
                severity="warning",
                code="runtime_space_saturated",
                message="Runtime space quotas are saturated.",
                count=len(saturated_spaces),
                metadata={
                    "runtime_space_ids": [str(space.runtime_space_id) for space in saturated_spaces]
                },
            )
        )

    degraded_providers = [
        provider for provider in runtime_capacity.providers if provider.degraded > 0
    ]
    if degraded_providers:
        issues.append(
            OperationsControlPlaneIssueResponse(
                severity="warning",
                code="runtime_provider_degraded",
                message="Runtime providers have degraded capacity.",
                count=sum(provider.degraded for provider in degraded_providers),
                metadata={
                    "providers": [
                        f"{provider.provider}:{provider.runtime_type}"
                        for provider in degraded_providers
                    ]
                },
            )
        )
