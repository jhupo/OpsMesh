from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.core.typing import dict_list
from backend.app.observability.audit_service import AuditService
from backend.app.tasks.collaboration_recovery_plan import (
    build_recovery_plan,
    dry_run_recovery_result,
    filter_recovery_plan,
    recovery_apply_status,
    recovery_instruction,
    recovery_plan_status,
    recovery_plan_summary,
    skipped_recovery_plan_items,
    uuid_list,
)
from backend.app.tasks.collaboration_state import TaskCollaborationStateService
from backend.app.tasks.execution_diagnostics import TaskExecutionDiagnosticsService
from backend.app.tasks.operator_actions import TaskOperatorActionService


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

        plan = build_recovery_plan(collaboration=collaboration, execution=execution)
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
            "status": recovery_plan_status(collaboration, selected),
            "summary": recovery_plan_summary(
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

        action_plan = filter_recovery_plan(
            dict_list(plan_payload.get("action_plan")),
            actions=actions,
            sources=sources,
        )
        skipped = [
            *dict_list(plan_payload.get("skipped")),
            *skipped_recovery_plan_items(
                dict_list(plan_payload.get("action_plan")),
                selected=action_plan,
                actions=actions,
                sources=sources,
            ),
        ]
        results = (
            [dry_run_recovery_result(item) for item in action_plan]
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
            "status": recovery_apply_status(dry_run, applied_count, failed_count, action_plan),
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
                    task_step_ids=uuid_list(item.get("task_step_ids")),
                    agent_profile_id=None,
                    instruction=recovery_instruction(item),
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

