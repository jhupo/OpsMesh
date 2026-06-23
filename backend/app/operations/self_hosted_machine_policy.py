from __future__ import annotations

from backend.app.core.typing import string_list
from backend.app.operations.utils import positive_int_or_none


def self_hosted_policy_summary(capabilities: dict[str, object]) -> dict[str, object]:
    return {
        "allowed_tools": string_list(capabilities.get("allowed_tools")),
        "supported_models": string_list(capabilities.get("supported_models")),
        "supported_runtimes": string_list(capabilities.get("supported_runtimes")),
        "supported_network_modes": string_list(capabilities.get("supported_network_modes")),
        "allowed_runtime_space_ids": string_list(capabilities.get("allowed_runtime_space_ids")),
        "max_concurrent_jobs": positive_int_or_none(capabilities.get("max_concurrent_jobs")),
        "max_concurrent_mcp_jobs": positive_int_or_none(
            capabilities.get("max_concurrent_mcp_jobs")
        ),
        "max_artifact_bytes": positive_int_or_none(capabilities.get("max_artifact_bytes")),
    }
