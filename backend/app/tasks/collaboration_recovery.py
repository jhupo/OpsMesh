from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.core.typing import dict_list, dict_or_empty, string_list
from backend.app.tasks.collaboration_state import TaskCollaborationStateService
from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.operator_actions import TASK_OPERATOR_ACTIONS, TaskOperatorActionService

RECOVERY_PLAN_SOURCES = {
    "collaboration_state",
    "handoff",
    "manager",
    "execution_diagnostics",
}


class TaskCollaborationRecoveryService:
    """Turn task collaboration diagnostics into executable recovery actions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_plan(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        max_actions: int = 10,
    ) -> dict[str, object] | None:
        collaboration = TaskCollaborationStateService(self._session).get_state(
            workspace_id=workspace_id,
            task_id=task_id,
        )
        if collaboration is None:
            return None
        execution = TaskExecutionDiagnosticsService(self._session).get_diagnostics(
            workspace_id=workspace_id,
            task_id=task_id,
        )
        if execution is None:
            return None

        plan = _build_plan(collaboration=collaboration, execution=execution)
        selected = plan[:max_actions]
        skipped = [
            {
                "action": item["action"],
                "source": item["source"],
                "reason": "max_actions_exceeded",
            }
            for item in plan[max_actions:]
        ]
        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "status": _plan_status(collaboration, selected),
            "summary": _summary(
                collaboration=collaboration,
                execution=execution,
                plan=selected,
                skipped=skipped,
            ),
            "collaboration": collaboration,
            "action_plan": selected,
            "skipped": skipped,
        }

    def apply_plan(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        dry_run: bool,
        actions: list[str] | None = None,
        sources: list[str] | None = None,
        max_actions: int = 10,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        plan_payload = self.get_plan(
            workspace_id=workspace_id,
            task_id=task_id,
            max_actions=max_actions,
        )
        if plan_payload is None:
            return None

        action_plan = _filter_plan(
            dict_list(plan_payload.get("action_plan")),
            actions=actions,
            sources=sources,
        )
        skipped = [
            *dict_list(plan_payload.get("skipped")),
            *_filter_skips(
                dict_list(plan_payload.get("action_plan")),
                selected=action_plan,
                actions=actions,
                sources=sources,
            ),
        ]
        results = (
            [_dry_run_result(item) for item in action_plan]
            if dry_run
            else self._apply_actions(
                workspace_id=workspace_id,
                task_id=task_id,
                actor_user_id=actor_user_id,
                action_plan=action_plan,
                reason=reason,
                metadata=metadata or {},
            )
        )
        applied_count = sum(1 for item in results if item["status"] == "applied")
        failed_count = sum(1 for item in results if item["status"] == "failed")
        if not dry_run:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="task.collaboration_recovery.actions_applied",
                target_type="task",
                target_id=task_id,
                metadata={
                    "requested_action_count": len(dict_list(plan_payload.get("action_plan"))),
                    "eligible_action_count": len(action_plan),
                    "applied_action_count": applied_count,
                    "failed_action_count": failed_count,
                    "skipped_action_count": len(skipped),
                    "actions": [str(item.get("action")) for item in action_plan],
                    "sources": sorted(
                        {
                            str(source)
                            for item in action_plan
                            if isinstance((source := item.get("source")), str)
                        }
                    ),
                    "reason": reason,
                    "metadata": metadata or {},
                },
            )
            self._session.commit()
        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "generated_at": datetime.now(UTC),
            "dry_run": dry_run,
            "status": _apply_status(dry_run, applied_count, failed_count, action_plan),
            "requested_action_count": len(dict_list(plan_payload.get("action_plan"))),
            "eligible_action_count": len(action_plan),
            "applied_action_count": applied_count,
            "failed_action_count": failed_count,
            "skipped_action_count": len(skipped),
            "summary": plan_payload["summary"],
            "results": results,
            "skipped": skipped,
        }

    def _apply_actions(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        actor_user_id: UUID,
        action_plan: list[dict[str, object]],
        reason: str | None,
        metadata: dict[str, object],
    ) -> list[dict[str, object]]:
        operator = TaskOperatorActionService(self._session)
        results: list[dict[str, object]] = []
        for item in action_plan:
            action = str(item["action"])
            try:
                response = operator.apply_action(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    actor_user_id=actor_user_id,
                    action=action,
                    task_step_ids=_uuid_list(item.get("task_step_ids")),
                    agent_profile_id=None,
                    instruction=_instruction(item),
                    reason=reason or str(item.get("reason") or "task_collaboration_recovery"),
                    metadata={
                        **metadata,
                        "collaboration_recovery_source": item.get("source"),
                        "collaboration_recovery_reason": item.get("reason"),
                        "collaboration_recovery_blocked_reasons": item.get("blocked_reasons"),
                    },
                )
            except ValueError as exc:
                self._session.rollback()
                results.append(
                    {
                        "action": action,
                        "source": item.get("source"),
                        "status": "failed",
                        "task_step_ids": item.get("task_step_ids", []),
                        "reason": str(exc),
                        "response": None,
                    }
                )
                continue
            results.append(
                {
                    "action": action,
                    "source": item.get("source"),
                    "status": response["status"] if response is not None else "noop",
                    "task_step_ids": item.get("task_step_ids", []),
                    "reason": item.get("reason"),
                    "response": response,
                }
            )
        return results


def _build_plan(
    *,
    collaboration: dict[str, object],
    execution: dict[str, object],
) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    handoff_source_step_ids: list[UUID] = []
    handoff_reasons: list[str] = []
    for handoff in dict_list(collaboration.get("handoffs")):
        status = handoff.get("status")
        if status != "ready_for_downstream":
            continue
        step_id = handoff.get("task_step_id")
        if isinstance(step_id, UUID):
            handoff_source_step_ids.append(step_id)
        handoff_reasons.append(str(status))
    if handoff_source_step_ids:
        items.append(
            _plan_item(
                action="schedule_downstream_steps",
                source="handoff",
                priority=90,
                reason="handoff_ready_for_downstream",
                task_step_ids=handoff_source_step_ids,
                blocked_reasons=["handoff_ready_for_downstream", *handoff_reasons],
            )
        )

    blocked_step_ids: list[UUID] = []
    blocked_reasons: list[str] = []
    for step in dict_list(execution.get("steps")):
        step_id = step.get("task_step_id")
        if step.get("status") != "blocked" or not isinstance(step_id, UUID):
            continue
        blocked_step_ids.append(step_id)
        blocked_reasons.extend(string_list(step.get("blocked_reasons")))
    if blocked_step_ids:
        items.append(
            _plan_item(
                action="requeue_blocked_steps",
                source="execution_diagnostics",
                priority=80,
                reason="blocked_steps_detected",
                task_step_ids=blocked_step_ids,
                blocked_reasons=blocked_reasons or ["blocked_steps_detected"],
            )
        )

    manager = dict_or_empty(collaboration.get("manager"))
    manager_reasons = string_list(manager.get("blocked_reasons"))
    if _needs_manager_review(manager_reasons):
        items.append(
            _plan_item(
                action="request_manager_review",
                source="manager",
                priority=70,
                reason="manager_protocol_needs_review",
                task_step_ids=[],
                blocked_reasons=manager_reasons,
            )
        )

    summary_reasons = string_list(
        dict_or_empty(collaboration.get("summary")).get("blocked_reasons")
    )
    if "downstream_blocked" in summary_reasons and not blocked_step_ids:
        blocked_downstream_ids = [
            step_id
            for handoff in dict_list(collaboration.get("handoffs"))
            for step_id in _uuid_list(handoff.get("blocked_downstream_step_ids"))
        ]
        if blocked_downstream_ids:
            items.append(
                _plan_item(
                    action="requeue_blocked_steps",
                    source="collaboration_state",
                    priority=85,
                    reason="downstream_blocked",
                    task_step_ids=blocked_downstream_ids,
                    blocked_reasons=["downstream_blocked"],
                )
            )

    return sorted(
        _dedupe_plan(items),
        key=lambda item: (-int(item["priority"]), str(item["action"])),
    )


def _plan_item(
    *,
    action: str,
    source: str,
    priority: int,
    reason: str,
    task_step_ids: list[UUID],
    blocked_reasons: list[str],
) -> dict[str, object]:
    task_step_ids = list(dict.fromkeys(task_step_ids))
    blocked_reasons = list(dict.fromkeys(blocked_reasons))
    return {
        "action": action,
        "source": source,
        "automation": "task_operator_action",
        "api_route": "POST /api/v1/workspaces/{workspace_id}/tasks/{task_id}/operator-actions",
        "priority": priority,
        "reason": reason,
        "task_step_ids": task_step_ids,
        "blocked_reasons": blocked_reasons,
        "payload_template": {
            "action": action,
            "task_step_ids": task_step_ids,
            "reason": reason,
            "metadata": {"source": "task_collaboration_recovery", "diagnostic_source": source},
        },
    }


def _dedupe_plan(items: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for item in items:
        key = (str(item["action"]), str(item["source"]))
        if key not in grouped:
            grouped[key] = item
            continue
        existing = grouped[key]
        existing["task_step_ids"] = list(
            dict.fromkeys(
                [
                    *_uuid_list(existing.get("task_step_ids")),
                    *_uuid_list(item.get("task_step_ids")),
                ]
            )
        )
        existing["blocked_reasons"] = list(
            dict.fromkeys(
                [
                    *string_list(existing.get("blocked_reasons")),
                    *string_list(item.get("blocked_reasons")),
                ]
            )
        )
        payload = dict_or_empty(existing.get("payload_template"))
        payload["task_step_ids"] = existing["task_step_ids"]
        existing["payload_template"] = payload
    return list(grouped.values())


def _filter_plan(
    plan: list[dict[str, object]],
    *,
    actions: list[str] | None,
    sources: list[str] | None,
) -> list[dict[str, object]]:
    allowed_actions = set(actions or TASK_OPERATOR_ACTIONS)
    allowed_sources = set(sources or RECOVERY_PLAN_SOURCES)
    return [
        item
        for item in plan
        if item.get("action") in allowed_actions and item.get("source") in allowed_sources
    ]


def _filter_skips(
    plan: list[dict[str, object]],
    *,
    selected: list[dict[str, object]],
    actions: list[str] | None,
    sources: list[str] | None,
) -> list[dict[str, object]]:
    selected_ids = {id(item) for item in selected}
    skipped: list[dict[str, object]] = []
    allowed_actions = set(actions or TASK_OPERATOR_ACTIONS)
    allowed_sources = set(sources or RECOVERY_PLAN_SOURCES)
    for item in plan:
        if id(item) in selected_ids:
            continue
        action = item.get("action")
        source = item.get("source")
        reason = "action_filtered" if action not in allowed_actions else "source_filtered"
        if source not in allowed_sources or action not in allowed_actions:
            skipped.append({"action": action, "source": source, "reason": reason})
    return skipped


def _summary(
    *,
    collaboration: dict[str, object],
    execution: dict[str, object],
    plan: list[dict[str, object]],
    skipped: list[dict[str, object]],
) -> dict[str, object]:
    action_counts = Counter(str(item["action"]) for item in plan)
    source_counts = Counter(str(item["source"]) for item in plan)
    return {
        "collaboration_status": dict_or_empty(collaboration.get("summary")).get("status"),
        "task_status": dict_or_empty(execution.get("task")).get("status"),
        "blocked_reasons": string_list(
            dict_or_empty(collaboration.get("summary")).get("blocked_reasons")
        ),
        "recommended_actions": string_list(
            dict_or_empty(collaboration.get("summary")).get("recommended_actions")
        ),
        "action_count": len(plan),
        "skipped_count": len(skipped),
        "action_counts": dict(sorted(action_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
    }


def _plan_status(collaboration: dict[str, object], plan: list[dict[str, object]]) -> str:
    if plan:
        return "actionable"
    summary = dict_or_empty(collaboration.get("summary"))
    if summary.get("status") in {"blocked", "needs_attention"}:
        return "needs_manual_attention"
    return "healthy"


def _apply_status(
    dry_run: bool,
    applied_count: int,
    failed_count: int,
    action_plan: list[dict[str, object]],
) -> str:
    if dry_run:
        return "dry_run"
    if failed_count:
        return "partial_failure" if applied_count else "failed"
    if applied_count:
        return "applied"
    return "noop" if not action_plan else "no_changes"


def _dry_run_result(item: dict[str, object]) -> dict[str, object]:
    return {
        "action": item.get("action"),
        "source": item.get("source"),
        "status": "would_apply",
        "task_step_ids": item.get("task_step_ids", []),
        "reason": item.get("reason"),
        "response": None,
    }


def _needs_manager_review(blocked_reasons: list[str]) -> bool:
    reasons = set(blocked_reasons)
    return bool(
        reasons
        & {
            "acceptance_decision_missing",
            "follow_up_missing",
            "missing_manager_summary_step",
            "follow_up_incomplete",
        }
    ) or any(reason.startswith("missing_manager_") for reason in reasons)


def _instruction(item: dict[str, object]) -> str | None:
    action = item.get("action")
    if action == "request_manager_review":
        return "Review the current collaboration state and decide the next delivery action."
    return None


def _uuid_list(value: object) -> list[UUID]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, UUID)]
