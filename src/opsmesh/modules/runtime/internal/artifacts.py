"""Merge durable delegated output receipts without retaining live execution resources."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from opsmesh.framework.errors import OpsMeshError


def merge_delegated_artifacts(existing: Any, incoming: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for receipts in (existing, incoming):
        if not isinstance(receipts, list):
            raise OpsMeshError("ARTIFACT_INVALID", "Delegated artifacts must be a receipt list")
        for receipt in receipts:
            if not isinstance(receipt, dict) or not isinstance(receipt.get("tool_call_id"), str):
                raise OpsMeshError("ARTIFACT_INVALID", "Delegated artifact needs a stable call ID")
            if not receipt["tool_call_id"]:
                raise OpsMeshError("ARTIFACT_INVALID", "Delegated artifact call ID is empty")
            merged[receipt["tool_call_id"]] = deepcopy(receipt)
    return list(merged.values())
