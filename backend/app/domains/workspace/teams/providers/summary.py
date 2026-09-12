from __future__ import annotations


def provider_readiness_summary(items: list[dict[str, object]]) -> dict[str, object]:
    blocking_items = _items_with_status(items, "blocked")
    degraded_items = _items_with_status(items, "degraded")
    runtime_items = [item for item in items if item["runtime_participant"] is True]
    runtime_blocking_items = [item for item in items if item["runtime_blocking"] is True]
    runtime_degraded_items = [item for item in items if item["runtime_degraded"] is True]

    return {
        "status": _overall_status(
            blocking_items=blocking_items,
            degraded_items=degraded_items,
            runtime_blocking_items=runtime_blocking_items,
        ),
        "member_count": len(items),
        "runtime_participant_count": len(runtime_items),
        "ready_member_count": _ready_count(items),
        "degraded_member_count": len(degraded_items),
        "blocked_member_count": len(blocking_items),
        "runtime_ready_member_count": _ready_count(runtime_items),
        "runtime_degraded_member_count": len(runtime_degraded_items),
        "runtime_blocked_member_count": len(runtime_blocking_items),
        "requires_operator_attention": bool(blocking_items or degraded_items),
        "blocking_reasons": _reason_counts(blocking_items),
        "warning_reasons": _reason_counts(degraded_items),
        "runtime_blocking_reasons": _reason_counts(runtime_blocking_items),
        "runtime_warning_reasons": _reason_counts(runtime_degraded_items),
        "members": items,
        "action_plan": _action_plan(
            runtime_blocking_items,
            [
                item
                for item in [*blocking_items, *degraded_items]
                if item not in runtime_blocking_items
            ],
        ),
    }


def _overall_status(
    *,
    blocking_items: list[dict[str, object]],
    degraded_items: list[dict[str, object]],
    runtime_blocking_items: list[dict[str, object]],
) -> str:
    if runtime_blocking_items:
        return "blocked"
    if blocking_items or degraded_items:
        return "degraded"
    return "healthy"


def _items_with_status(
    items: list[dict[str, object]],
    status: str,
) -> list[dict[str, object]]:
    return [item for item in items if item["readiness_status"] == status]


def _ready_count(items: list[dict[str, object]]) -> int:
    return sum(1 for item in items if item["readiness_status"] == "ready")


def _reason_counts(items: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        values = item.get("reasons")
        if not values:
            values = item.get("warnings")
        if not isinstance(values, list):
            continue
        for reason in values:
            if isinstance(reason, str):
                counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _action_plan(
    blocking_items: list[dict[str, object]],
    degraded_items: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        *_blocking_actions(blocking_items),
        *_degraded_actions(degraded_items, offset=len(blocking_items)),
    ]


def _blocking_actions(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            **_action_base(item, source_index=index),
            "priority": 100,
            "reason": "team_model_provider_blocked",
            "reasons": item["reasons"],
        }
        for index, item in enumerate(items)
    ]


def _degraded_actions(
    items: list[dict[str, object]],
    *,
    offset: int,
) -> list[dict[str, object]]:
    return [
        {
            **_action_base(item, source_index=offset + index),
            "priority": 60,
            "reason": "team_model_provider_degraded",
            "warnings": item["warnings"],
            "reasons": item["reasons"],
        }
        for index, item in enumerate(items[:5])
    ]


def _action_base(
    item: dict[str, object],
    *,
    source_index: int,
) -> dict[str, object]:
    return {
        "source": "provider_readiness",
        "source_index": source_index,
        "automation": "team_runtime_control",
        "action": "review_model_provider",
        "agent_profile_id": item["agent_profile_id"],
        "team_member_id": item["team_member_id"],
        "credential_id": item["credential_id"],
        "credential_reference": item["credential_reference"],
        "provider": item["provider"],
        "model": item["model"],
        "model_api": item["model_api"],
        "model_apis": item["model_apis"],
        "default_model_api": item["default_model_api"],
        "failure_count": item.get("failure_count", 0),
        "last_failure_at": item.get("last_failure_at"),
        "last_failure_code": item.get("last_failure_code"),
        "last_failure_message": item.get("last_failure_message"),
        "task_ids": [],
        "task_step_ids": [],
    }
