from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.audit.service import AuditService
from backend.app.orchestration.runs import RunOrchestrationService
from backend.app.runs.models import AgentRun
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError
from backend.app.runtime_manager.safety import RuntimeSafetyError
from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.manager_diagnostics import TaskManagerDiagnosticsService
from backend.app.teams.execution_overview import TeamExecutionOverviewService
from backend.app.teams.operator_actions import TEAM_OPERATOR_ACTIONS, TeamOperatorActionService
from backend.app.teams.provider_readiness import TeamProviderReadinessService
from backend.app.teams.runtime import (
    TEAM_RUNTIME_RUNNING,
    TEAM_RUNTIME_STALL_THRESHOLD,
    TeamRuntimeService,
)
from backend.app.teams.scheduling_blocks import scheduled_run_blocking_summary
from backend.app.workers.queue import RedisQueue

COMMAND_CENTER_ACTION_SOURCES = {
    "execution_overview",
    "handoff_queue",
    "manager_queue",
    "provider_readiness",
    "team_runtime",
}
RUNTIME_OPERATOR_ACTIONS = {
    "ensure_team_runtime",
    "review_model_provider",
    "review_team_runtime_stall",
    "start_team_runtime",
}
NON_APPLICABLE_RUNTIME_ACTIONS = {"review_model_provider", "review_team_runtime_stall"}


class TeamCommandCenterService:
    """Compose team execution diagnostics into a single operator-facing view."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_command_center(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        include_completed: bool = False,
        queue_limit: int = 50,
    ) -> dict[str, object] | None:
        overview = TeamExecutionOverviewService(self._session).get_overview(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
        )
        if overview is None:
            return None

        handoff_queue = TaskExecutionDiagnosticsService(self._session).list_handoff_queue(
            workspace_id=workspace_id,
            limit=queue_limit,
            offset=0,
            team_id=team_id,
            include_terminal=False,
        )
        manager_queue = TaskManagerDiagnosticsService(self._session).list_manager_queue(
            workspace_id=workspace_id,
            limit=queue_limit,
            offset=0,
            team_id=team_id,
            include_healthy=False,
        )
        action_plan = _merged_action_plan(
            overview=overview,
            handoff_queue=handoff_queue,
            manager_queue=manager_queue,
        )
        runtime_state = TeamRuntimeService(self._session).get_state(
            workspace_id=workspace_id,
            team_id=team_id,
        )
        if runtime_state is None:
            return None
        provider_readiness = TeamProviderReadinessService(self._session).get_readiness(
            workspace_id=workspace_id,
            team_id=team_id,
        )
        provider_action_plan = _provider_action_plan(provider_readiness)
        runtime_action_plan = _runtime_action_plan(runtime_state)
        action_plan = [*provider_action_plan, *runtime_action_plan, *action_plan]
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "summary": _summary(
                overview=overview,
                handoff_queue=handoff_queue,
                manager_queue=manager_queue,
                runtime_state=runtime_state,
                provider_readiness=provider_readiness,
                action_plan=action_plan,
                queue_limit=queue_limit,
            ),
            "runtime": _runtime_payload(
                runtime_state,
                provider_readiness=provider_readiness,
            ),
            "provider_readiness": provider_readiness,
            "operating_policy": getattr(runtime_state, "operating_policy", {}),
            "memory_summary": getattr(runtime_state, "memory_summary", {}),
            "overview": overview,
            "queues": {
                "handoff": handoff_queue,
                "manager": manager_queue,
            },
            "action_plan": action_plan,
        }

    def apply_action_plan(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        include_completed: bool = False,
        queue_limit: int = 50,
        dry_run: bool = True,
        sources: list[str] | None = None,
        actions: list[str] | None = None,
        max_actions: int = 5,
        max_tasks_per_action: int = 100,
        enqueue_runs: bool = False,
        queue: RedisQueue | None = None,
        runtime_control: RuntimeControlService | None = None,
        reason: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        command_center = self.get_command_center(
            workspace_id=workspace_id,
            team_id=team_id,
            include_completed=include_completed,
            queue_limit=queue_limit,
        )
        if command_center is None:
            return None

        action_plan = _list(command_center.get("action_plan"))
        grouped, skipped = _group_applicable_actions(
            action_plan=action_plan,
            sources=sources,
            actions=actions,
            max_actions=max_actions,
        )
        results = (
            [
                {
                    "action": item["action"],
                    "automation": item["automation"],
                    "status": "would_apply",
                    "sources": item["sources"],
                    "task_ids": item["task_ids"],
                    "task_step_ids": item["task_step_ids"],
                    "agent_profile_id": item["agent_profile_id"],
                    "candidate_count": item["candidate_count"],
                    "response": None,
                }
                for item in grouped
            ]
            if dry_run
            else self._apply_grouped_actions(
                workspace_id=workspace_id,
                team_id=team_id,
                actor_user_id=actor_user_id,
                grouped=grouped,
                max_tasks_per_action=max_tasks_per_action,
                runtime_control=runtime_control,
                reason=reason,
                metadata=metadata or {},
            )
        )
        provider_readiness = _dict(command_center.get("provider_readiness"))
        scheduled_run_skip_reason = (
            "provider_readiness_blocked"
            if _provider_readiness_blocked(provider_readiness)
            else None
        )
        scheduled_runs = (
            RunOrchestrationService(self._session, queue=queue).schedule_team_steps(
                workspace_id=workspace_id,
                team_id=team_id,
                requested_by_user_id=actor_user_id,
            )
            if enqueue_runs and not dry_run and scheduled_run_skip_reason is None
            else []
        )
        scheduled_run_blocking = (
            scheduled_run_blocking_summary(
                self._session,
                workspace_id=workspace_id,
                team_id=team_id,
            )
            if enqueue_runs and not dry_run
            else {"blocked_reasons": {}, "blocked_steps": []}
        )
        applied_action_count = sum(1 for item in results if item["status"] == "applied")
        if not dry_run:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="team.command_center.actions_applied",
                target_type="agent_team",
                target_id=team_id,
                metadata={
                    "eligible_action_count": len(grouped),
                    "applied_action_count": applied_action_count,
                    "skipped_action_count": len(skipped),
                    "scheduled_run_count": len(scheduled_runs),
                    "scheduled_run_skip_reason": scheduled_run_skip_reason,
                    "scheduled_run_blocked_reasons": scheduled_run_blocking[
                        "blocked_reasons"
                    ],
                    "scheduled_run_blocked_steps": scheduled_run_blocking["blocked_steps"],
                    "actions": [str(item["action"]) for item in results],
                    "sources": sorted(
                        {
                            str(source)
                            for item in results
                            for source in _string_list(item.get("sources"))
                        }
                    ),
                },
            )
            self._session.commit()
        return {
            "workspace_id": workspace_id,
            "team_id": team_id,
            "generated_at": datetime.now(UTC),
            "dry_run": dry_run,
            "status": "dry_run" if dry_run else "applied" if applied_action_count else "noop",
            "requested_action_count": len(action_plan),
            "eligible_action_count": len(grouped),
            "applied_action_count": applied_action_count,
            "skipped_action_count": len(skipped),
            "summary": command_center["summary"],
            "results": results,
            "skipped": skipped,
            "scheduled_run_skip_reason": scheduled_run_skip_reason,
            "scheduled_run_blocked_reasons": scheduled_run_blocking["blocked_reasons"],
            "scheduled_run_blocked_steps": scheduled_run_blocking["blocked_steps"],
            "scheduled_run_count": len(scheduled_runs),
            "scheduled_runs": [_scheduled_run_payload(run) for run in scheduled_runs],
        }

    def _apply_grouped_actions(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        grouped: list[dict[str, object]],
        max_tasks_per_action: int,
        runtime_control: RuntimeControlService | None,
        reason: str | None,
        metadata: dict[str, object],
    ) -> list[dict[str, object]]:
        operator_actions = TeamOperatorActionService(self._session)
        results: list[dict[str, object]] = []
        for item in grouped:
            if item["automation"] == "team_runtime_control":
                results.append(
                    self._apply_runtime_action(
                        workspace_id=workspace_id,
                        team_id=team_id,
                        actor_user_id=actor_user_id,
                        item=item,
                        runtime_control=runtime_control,
                        reason=reason,
                        metadata=metadata,
                    )
                )
                continue
            response = operator_actions.apply_action(
                workspace_id=workspace_id,
                team_id=team_id,
                actor_user_id=actor_user_id,
                action=str(item["action"]),
                task_ids=_uuid_list(item.get("task_ids")),
                task_step_ids=_uuid_list(item.get("task_step_ids")),
                agent_profile_id=_uuid_value(item.get("agent_profile_id")),
                max_tasks=max_tasks_per_action,
                instruction=None,
                reason=reason or "team_command_center",
                metadata={
                    **metadata,
                    "command_center_sources": item["sources"],
                    "command_center_candidate_count": item["candidate_count"],
                },
            )
            results.append(
                {
                    "action": item["action"],
                    "automation": item["automation"],
                    "status": response["status"] if response is not None else "noop",
                    "sources": item["sources"],
                    "task_ids": item["task_ids"],
                    "task_step_ids": item["task_step_ids"],
                    "agent_profile_id": item["agent_profile_id"],
                    "candidate_count": item["candidate_count"],
                    "response": response,
                }
            )
        return results

    def _apply_runtime_action(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        item: dict[str, object],
        runtime_control: RuntimeControlService | None,
        reason: str | None,
        metadata: dict[str, object],
    ) -> dict[str, object]:
        action = str(item["action"])
        service = TeamRuntimeService(self._session)
        try:
            if action in NON_APPLICABLE_RUNTIME_ACTIONS:
                return _runtime_action_result(item, "skipped", "manual_operator_review_required")
            if action == "start_team_runtime":
                if runtime_control is None:
                    return _runtime_action_result(item, "skipped", "runtime_control_unavailable")
                state = service.start(
                    workspace_id=workspace_id,
                    team_id=team_id,
                    actor_user_id=actor_user_id,
                    runtime_control=runtime_control,
                    reason=reason or "team_command_center_start_runtime",
                    metadata=metadata,
                )
            elif action == "ensure_team_runtime":
                if runtime_control is None:
                    return _runtime_action_result(item, "skipped", "runtime_control_unavailable")
                state = service.ensure_workspace_runtime(
                    workspace_id=workspace_id,
                    team_id=team_id,
                    actor_user_id=actor_user_id,
                    runtime_control=runtime_control,
                    start=True,
                    reason=reason or "team_command_center_ensure_runtime",
                    metadata=metadata,
                )
            else:
                return _runtime_action_result(item, "skipped", "unsupported_runtime_action")
        except (RuntimeQuotaExceededError, RuntimeSafetyError, ValueError) as exc:
            return _runtime_action_result(item, "skipped", str(exc))
        if state is None:
            return _runtime_action_result(item, "noop", "team_not_found")
        return {
            "action": action,
            "automation": item["automation"],
            "status": "applied",
            "sources": item["sources"],
            "task_ids": item["task_ids"],
            "task_step_ids": item["task_step_ids"],
            "agent_profile_id": item["agent_profile_id"],
            "candidate_count": item["candidate_count"],
            "response": _runtime_payload(state),
        }


def _summary(
    *,
    overview: dict[str, object],
    handoff_queue: dict[str, object],
    manager_queue: dict[str, object],
    runtime_state: object,
    provider_readiness: dict[str, object],
    action_plan: list[dict[str, object]],
    queue_limit: int,
) -> dict[str, object]:
    overview_summary = _dict(overview.get("summary"))
    source_counts: dict[str, int] = {}
    for item in action_plan:
        source = item.get("source")
        if isinstance(source, str):
            source_counts[source] = source_counts.get(source, 0) + 1

    return {
        "team_status": _dict(overview.get("team")).get("status"),
        "delivery_health": overview_summary.get("delivery_health"),
        "total_tasks": _int(overview_summary.get("total_tasks")),
        "needs_attention_tasks": _int(overview_summary.get("needs_attention_tasks")),
        "blocked_tasks": _int(overview_summary.get("blocked_tasks")),
        "handoff_queue_total": _int(handoff_queue.get("total")),
        "manager_queue_total": _int(manager_queue.get("total")),
        "runtime_status": getattr(runtime_state, "status", None),
        "workspace_runtime_id": getattr(runtime_state, "workspace_runtime_id", None),
        "workspace_runtime_status": getattr(runtime_state, "runtime_status", None),
        "runtime_space_id": getattr(runtime_state, "runtime_space_id", None),
        "runtime_ready": _runtime_ready(runtime_state),
        "provider_readiness": _provider_readiness_summary(provider_readiness),
        "memory_entry_count": _int(
            _dict(getattr(runtime_state, "memory_summary", {})).get("active_entry_count")
        ),
        "team_memory_entry_count": _int(
            _dict(getattr(runtime_state, "memory_summary", {})).get("team_entry_count")
        ),
        "queue_limit": queue_limit,
        "queue_truncated": (
            _int(handoff_queue.get("total")) > queue_limit
            or _int(manager_queue.get("total")) > queue_limit
        ),
        "action_plan_count": len(action_plan),
        "action_plan_source_counts": dict(sorted(source_counts.items())),
    }


def _provider_action_plan(provider_readiness: dict[str, object]) -> list[dict[str, object]]:
    return _source_action_plan(
        source="provider_readiness",
        items=_list(provider_readiness.get("action_plan")),
    )


def _merged_action_plan(
    *,
    overview: dict[str, object],
    handoff_queue: dict[str, object],
    manager_queue: dict[str, object],
) -> list[dict[str, object]]:
    return [
        *_source_action_plan(
            source="execution_overview",
            items=_list(_dict(overview.get("summary")).get("intervention_plan")),
        ),
        *_source_action_plan(
            source="handoff_queue",
            items=_list(_dict(handoff_queue.get("summary")).get("team_operator_action_plan")),
        ),
        *_source_action_plan(
            source="manager_queue",
            items=_list(_dict(manager_queue.get("summary")).get("team_operator_action_plan")),
        ),
    ]


def _runtime_action_plan(runtime_state: object) -> list[dict[str, object]]:
    status = getattr(runtime_state, "status", None)
    workspace_runtime_id = getattr(runtime_state, "workspace_runtime_id", None)
    runtime_status = getattr(runtime_state, "runtime_status", None)
    metadata = _dict(getattr(runtime_state, "metadata", {}))
    stall_count = _int(metadata.get("stall_count"))
    stalled_at = metadata.get("stalled_at")
    if stalled_at or stall_count >= TEAM_RUNTIME_STALL_THRESHOLD:
        return [
            {
                "source": "team_runtime",
                "source_index": 0,
                "automation": "team_runtime_control",
                "action": "review_team_runtime_stall",
                "priority": 120,
                "reason": metadata.get("stall_reason") or "team_runtime_stalled",
                "stall_count": stall_count,
                "stall_threshold": _int(metadata.get("stall_threshold"))
                or TEAM_RUNTIME_STALL_THRESHOLD,
                "stalled_at": stalled_at,
                "task_ids": [],
                "task_step_ids": [],
            }
        ]
    if status != TEAM_RUNTIME_RUNNING:
        return [
            {
                "source": "team_runtime",
                "source_index": 0,
                "automation": "team_runtime_control",
                "action": "start_team_runtime",
                "priority": 90,
                "reason": "team_runtime_not_running",
                "task_ids": [],
                "task_step_ids": [],
            }
        ]
    if workspace_runtime_id is None or runtime_status != "running":
        return [
            {
                "source": "team_runtime",
                "source_index": 0,
                "automation": "team_runtime_control",
                "action": "ensure_team_runtime",
                "priority": 95,
                "reason": "workspace_runtime_not_running",
                "workspace_runtime_id": workspace_runtime_id,
                "runtime_status": runtime_status,
                "task_ids": [],
                "task_step_ids": [],
            }
        ]
    return []


def _runtime_payload(
    runtime_state: object,
    *,
    provider_readiness: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "status": getattr(runtime_state, "status", None),
        "workspace_runtime_id": getattr(runtime_state, "workspace_runtime_id", None),
        "runtime_status": getattr(runtime_state, "runtime_status", None),
        "runtime_space_id": getattr(runtime_state, "runtime_space_id", None),
        "thread_id": getattr(runtime_state, "thread_id", None),
        "team_session_id": getattr(runtime_state, "team_session_id", None),
        "member_session_count": getattr(runtime_state, "member_session_count", 0),
        "ready": _runtime_ready(runtime_state),
        "provider_readiness": _provider_readiness_summary(provider_readiness or {}),
        "operating_policy": getattr(runtime_state, "operating_policy", {}),
        "memory_summary": getattr(runtime_state, "memory_summary", {}),
    }


def _provider_readiness_summary(provider_readiness: dict[str, object]) -> dict[str, object]:
    return {
        "status": provider_readiness.get("status", "unknown"),
        "member_count": _int(provider_readiness.get("member_count")),
        "runtime_participant_count": _int(provider_readiness.get("runtime_participant_count")),
        "ready_member_count": _int(provider_readiness.get("ready_member_count")),
        "degraded_member_count": _int(provider_readiness.get("degraded_member_count")),
        "blocked_member_count": _int(provider_readiness.get("blocked_member_count")),
        "runtime_ready_member_count": _int(
            provider_readiness.get("runtime_ready_member_count")
        ),
        "runtime_degraded_member_count": _int(
            provider_readiness.get("runtime_degraded_member_count")
        ),
        "runtime_blocked_member_count": _int(
            provider_readiness.get("runtime_blocked_member_count")
        ),
        "requires_operator_attention": provider_readiness.get(
            "requires_operator_attention",
            False,
        )
        is True,
        "blocking_reasons": _dict(provider_readiness.get("blocking_reasons")),
        "warning_reasons": _dict(provider_readiness.get("warning_reasons")),
        "runtime_blocking_reasons": _dict(
            provider_readiness.get("runtime_blocking_reasons")
        ),
        "runtime_warning_reasons": _dict(provider_readiness.get("runtime_warning_reasons")),
    }


def _runtime_ready(runtime_state: object) -> bool:
    return (
        getattr(runtime_state, "status", None) == TEAM_RUNTIME_RUNNING
        and getattr(runtime_state, "workspace_runtime_id", None) is not None
        and getattr(runtime_state, "runtime_status", None) == "running"
    )


def _provider_readiness_blocked(provider_readiness: dict[str, object]) -> bool:
    return _int(provider_readiness.get("runtime_blocked_member_count")) > 0


def _source_action_plan(
    *,
    source: str,
    items: list[object],
) -> list[dict[str, object]]:
    action_plan: list[dict[str, object]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        action_plan.append(
            {
                **item,
                "source": source,
                "source_index": index,
            }
        )
    return action_plan


def _group_applicable_actions(
    *,
    action_plan: list[object],
    sources: list[str] | None,
    actions: list[str] | None,
    max_actions: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    allowed_sources = set(sources or COMMAND_CENTER_ACTION_SOURCES)
    allowed_actions = set(actions or TEAM_OPERATOR_ACTIONS | RUNTIME_OPERATOR_ACTIONS)
    grouped: dict[str, dict[str, object]] = {}
    skipped: list[dict[str, object]] = []

    for index, item in enumerate(action_plan):
        if not isinstance(item, dict):
            skipped.append(_skip(index, "invalid_action_plan_item", item))
            continue
        source = item.get("source")
        action = item.get("action")
        if not isinstance(source, str) or source not in COMMAND_CENTER_ACTION_SOURCES:
            skipped.append(_skip(index, "unsupported_source", item))
            continue
        if source not in allowed_sources:
            continue
        if action in NON_APPLICABLE_RUNTIME_ACTIONS:
            skipped.append(_skip(index, "manual_operator_review_required", item))
            continue
        if not isinstance(action, str) or action not in (
            TEAM_OPERATOR_ACTIONS | RUNTIME_OPERATOR_ACTIONS
        ):
            skipped.append(_skip(index, "unsupported_action", item))
            continue
        if action not in allowed_actions:
            continue
        automation = item.get("automation")
        if automation not in {"team_operator_action", "team_runtime_control"}:
            skipped.append(_skip(index, "unsupported_automation", item))
            continue
        if automation == "team_runtime_control" and action not in RUNTIME_OPERATOR_ACTIONS:
            skipped.append(_skip(index, "unsupported_runtime_action", item))
            continue
        if automation == "team_operator_action" and action not in TEAM_OPERATOR_ACTIONS:
            skipped.append(_skip(index, "unsupported_operator_action", item))
            continue

        task_step_ids = _uuid_list(item.get("task_step_ids"))
        agent_profile_id = _uuid_value(item.get("agent_profile_id"))
        if automation == "team_operator_action" and action == "reassign_step":
            if agent_profile_id is None:
                skipped.append(_skip(index, "missing_reassign_agent", item))
                continue
            if len(task_step_ids) != 1:
                skipped.append(_skip(index, "invalid_reassign_step_count", item))
                continue

        group_key = (
            f"{action}:{agent_profile_id}:{task_step_ids[0]}"
            if action == "reassign_step"
            else action
        )
        group = grouped.setdefault(
            group_key,
            {
                "action": action,
                "automation": automation,
                "agent_profile_id": agent_profile_id,
                "sources": [],
                "task_ids": [],
                "task_step_ids": [],
                "candidate_count": 0,
                "max_priority": 0,
            },
        )
        _append_strings(group, "sources", source)
        _extend_uuids(group, "task_ids", _uuid_list(item.get("task_ids")))
        _extend_uuids(group, "task_step_ids", task_step_ids)
        group["candidate_count"] = int(group["candidate_count"]) + 1
        group["max_priority"] = max(int(group["max_priority"]), _int(item.get("priority")))

    ordered = sorted(
        grouped.values(),
        key=lambda item: (-int(item["max_priority"]), str(item["action"])),
    )
    selected = ordered[:max_actions]
    for item in ordered[max_actions:]:
        skipped.append(
            {
                "source_index": None,
                "source": None,
                "action": item["action"],
                "reason": "max_actions_exceeded",
            }
        )
    return selected, skipped


def _skip(index: int, reason: str, item: dict[str, object] | object) -> dict[str, object]:
    payload = item if isinstance(item, dict) else {}
    return {
        "source_index": index,
        "source": payload.get("source"),
        "action": payload.get("action"),
        "reason": reason,
    }


def _runtime_action_result(
    item: dict[str, object],
    status: str,
    reason: str,
) -> dict[str, object]:
    return {
        "action": item["action"],
        "automation": item["automation"],
        "status": status,
        "sources": item["sources"],
        "task_ids": item["task_ids"],
        "task_step_ids": item["task_step_ids"],
        "agent_profile_id": item["agent_profile_id"],
        "candidate_count": item["candidate_count"],
        "response": {"reason": reason},
    }


def _scheduled_run_payload(run: AgentRun) -> dict[str, object]:
    return {
        "agent_run_id": run.id,
        "task_id": run.task_id,
        "task_step_id": run.task_step_id,
        "agent_profile_id": run.agent_profile_id,
        "runtime_space_id": run.runtime_space_id,
        "status": run.status,
    }


def _append_strings(target: dict[str, object], key: str, value: str) -> None:
    values = target.setdefault(key, [])
    if not isinstance(values, list):
        return
    if value not in values:
        values.append(value)


def _extend_uuids(target: dict[str, object], key: str, values: list[UUID]) -> None:
    target_values = target.setdefault(key, [])
    if not isinstance(target_values, list):
        return
    existing = set(_uuid_list(target_values))
    for value in values:
        if value not in existing:
            target_values.append(value)
            existing.add(value)


def _dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _uuid_list(value: object) -> list[UUID]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, UUID)]


def _uuid_value(value: object) -> UUID | None:
    return value if isinstance(value, UUID) else None


def _json_safe_payload(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe_payload(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe_payload(item) for item in value]
    return str(value)
