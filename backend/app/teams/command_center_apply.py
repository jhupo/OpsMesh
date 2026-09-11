from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.runtime_manager.lifecycle.control import RuntimeLifecycleControl
from backend.app.runtime_manager.quotas import RuntimeQuotaExceededError
from backend.app.runtime_manager.safety import RuntimeSafetyError
from backend.app.teams.command_center_constants import NON_APPLICABLE_RUNTIME_ACTIONS
from backend.app.teams.command_center_grouping import _runtime_action_result
from backend.app.teams.command_center_payloads import _runtime_payload
from backend.app.teams.command_center_utils import _uuid_list, _uuid_value
from backend.app.teams.operator_actions import TeamOperatorActionService
from backend.app.teams.runtime import TeamRuntimeService


class TeamCommandCenterActionApplier:
    def __init__(self, session: Session) -> None:
        self._session = session

    def apply_grouped_actions(
        self,
        *,
        workspace_id: UUID,
        team_id: UUID,
        actor_user_id: UUID,
        grouped: list[dict[str, object]],
        max_tasks_per_action: int,
        runtime_control: RuntimeLifecycleControl | None,
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
        runtime_control: RuntimeLifecycleControl | None,
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
