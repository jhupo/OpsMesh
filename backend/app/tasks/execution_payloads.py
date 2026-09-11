from uuid import UUID

from backend.app.agents.models import AgentProfile
from backend.app.runs.models import AgentRun
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.tasks.models import TaskStep


def build_step_payload(
    step: TaskStep,
    *,
    agents: dict[UUID, AgentProfile],
    step_by_id: dict[UUID, TaskStep],
    runs: list[AgentRun],
    active_run_statuses: set[str],
) -> dict[str, object]:
    dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
    visible_dependencies = redact_sensitive_payload(dependencies)
    dependency_state = dependency_state_for_step(step, step_by_id)
    assigned_agent = (
        agents.get(step.assigned_agent_profile_id)
        if step.assigned_agent_profile_id is not None
        else None
    )
    active_runs = [run for run in runs if run.status in active_run_statuses]
    blocked_reasons = step_blocked_reasons(
        step=step,
        assigned_agent=assigned_agent,
        dependency_state=dependency_state,
        active_runs=active_runs,
    )
    return {
        "task_step_id": step.id,
        "work_package_id": step.work_package_id,
        "title": step.title,
        "description": step.description,
        "status": step.status,
        "order_index": step.order_index,
        "required_role": step.required_role,
        "required_skills": step.required_skills,
        "expected_artifacts": step.expected_artifacts,
        "acceptance_criteria": step.acceptance_criteria,
        "review_policy": redact_sensitive_payload(step.review_policy),
        "dependencies": visible_dependencies,
        "dependency_state": dependency_state,
        "assigned_agent": agent_payload(assigned_agent),
        "assignment_status": assignment_status(step, assigned_agent),
        "runnable": step.status == "queued" and not blocked_reasons,
        "blocked_reasons": blocked_reasons,
        "scheduling": {
            "status": visible_dependencies.get("scheduling_status"),
            "blocked_reason": visible_dependencies.get("blocked_reason"),
            "blocked_resource_keys": visible_dependencies.get("blocked_resource_keys"),
            "priority_score": visible_dependencies.get("priority_score"),
            "scheduled_at": visible_dependencies.get("scheduled_at"),
        },
        "runs": [run_payload(run) for run in runs],
        "active_run_ids": [run.id for run in active_runs],
        "result_summary": step.result_summary,
        "result_payload": redact_sensitive_payload(step.result_payload)
        if step.result_payload is not None
        else None,
    }


def dependency_state_for_step(
    step: TaskStep,
    step_by_id: dict[UUID, TaskStep],
) -> dict[str, object]:
    after_step_ids = uuid_list_from_dependencies(step.dependencies, "after_step_ids")
    missing_step_ids = [step_id for step_id in after_step_ids if step_id not in step_by_id]
    incomplete_step_ids = [
        step_id
        for step_id in after_step_ids
        if step_id in step_by_id and step_by_id[step_id].status != "completed"
    ]
    return {
        "after_step_ids": after_step_ids,
        "satisfied": not missing_step_ids and not incomplete_step_ids,
        "missing_step_ids": missing_step_ids,
        "incomplete_step_ids": incomplete_step_ids,
    }


def downstream_map(steps: list[TaskStep]) -> dict[UUID, list[UUID]]:
    step_ids = {step.id for step in steps}
    downstream_by_step_id: dict[UUID, list[UUID]] = {step.id: [] for step in steps}
    for step in steps:
        for upstream_step_id in uuid_list_from_dependencies(
            step.dependencies,
            "after_step_ids",
        ):
            if upstream_step_id in step_ids:
                downstream_by_step_id[upstream_step_id].append(step.id)
    return downstream_by_step_id


def handoff_state(
    payload: dict[str, object],
    *,
    step_payload_by_id: dict[UUID, dict[str, object]],
    downstream_by_step_id: dict[UUID, list[UUID]],
) -> dict[str, object]:
    step_id = payload["task_step_id"]
    if not isinstance(step_id, UUID):
        return {}
    dependency_state = payload.get("dependency_state")
    upstream_step_ids = (
        dependency_state.get("after_step_ids", []) if isinstance(dependency_state, dict) else []
    )
    downstream_step_ids = downstream_by_step_id.get(step_id, [])
    downstream_steps = [
        step_payload_by_id[downstream_step_id]
        for downstream_step_id in downstream_step_ids
        if downstream_step_id in step_payload_by_id
    ]
    blocked_downstream_step_ids = [
        downstream_step["task_step_id"]
        for downstream_step in downstream_steps
        if _has_handoff_blocker(downstream_step.get("blocked_reasons"))
    ]
    completed_downstream_step_ids = [
        downstream_step["task_step_id"]
        for downstream_step in downstream_steps
        if downstream_step.get("status") == "completed"
    ]
    waiting_downstream_step_ids = [
        downstream_step["task_step_id"]
        for downstream_step in downstream_steps
        if downstream_step.get("status") != "completed"
        and not downstream_step.get("blocked_reasons")
    ]
    runnable_downstream_step_ids = [
        downstream_step["task_step_id"]
        for downstream_step in downstream_steps
        if downstream_step.get("runnable") is True
    ]
    status = handoff_status(
        step_status=str(payload.get("status")),
        has_downstream=bool(downstream_steps),
        downstream_steps=downstream_steps,
        blocked_downstream_step_ids=blocked_downstream_step_ids,
        runnable_downstream_step_ids=runnable_downstream_step_ids,
    )
    return {
        "status": status,
        "requires_handoff": bool(downstream_steps),
        "upstream_step_ids": upstream_step_ids,
        "downstream_step_ids": downstream_step_ids,
        "completed_downstream_step_ids": completed_downstream_step_ids,
        "waiting_downstream_step_ids": waiting_downstream_step_ids,
        "blocked_downstream_step_ids": blocked_downstream_step_ids,
        "runnable_downstream_step_ids": runnable_downstream_step_ids,
        "deliverables_expected": payload.get("expected_artifacts", []),
        "has_result_summary": payload.get("result_summary") is not None,
        "recommended_actions": handoff_recommended_actions(
            status=status,
            has_result_summary=payload.get("result_summary") is not None,
        ),
    }


def _has_handoff_blocker(value: object) -> bool:
    if not isinstance(value, list):
        return False
    return any(reason != "active_run_exists" for reason in value if isinstance(reason, str))


def handoff_status(
    *,
    step_status: str,
    has_downstream: bool,
    downstream_steps: list[dict[str, object]],
    blocked_downstream_step_ids: list[object],
    runnable_downstream_step_ids: list[object],
) -> str:
    if step_status != "completed":
        return "source_incomplete" if has_downstream else "no_downstream"
    if not has_downstream:
        return "final_delivery_ready"
    if len(downstream_steps) == sum(
        1 for downstream_step in downstream_steps if downstream_step.get("status") == "completed"
    ):
        return "consumed"
    if blocked_downstream_step_ids:
        return "downstream_blocked"
    if runnable_downstream_step_ids:
        return "ready_for_downstream"
    return "handoff_in_progress"


def handoff_recommended_actions(
    *,
    status: str,
    has_result_summary: bool,
) -> list[str]:
    if status == "source_incomplete":
        return ["wait_for_source_completion"]
    if status == "ready_for_downstream":
        return ["schedule_downstream_steps"]
    if status == "downstream_blocked":
        return ["inspect_blocked_downstream"]
    if status == "final_delivery_ready":
        actions = ["request_manager_review"]
        if not has_result_summary:
            actions.append("create_correction")
        return actions
    if status == "no_downstream" and not has_result_summary:
        return ["create_correction"]
    return []


def step_blocked_reasons(
    *,
    step: TaskStep,
    assigned_agent: AgentProfile | None,
    dependency_state: dict[str, object],
    active_runs: list[AgentRun],
) -> list[str]:
    reasons: list[str] = []
    if step.status != "queued":
        if active_runs:
            reasons.append("active_run_exists")
        return reasons
    if step.assigned_agent_profile_id is None:
        reasons.append("agent_unassigned")
    elif assigned_agent is None:
        reasons.append("assigned_agent_missing")
    elif assigned_agent.status != "active":
        reasons.append("assigned_agent_inactive")
    if dependency_state["missing_step_ids"]:
        reasons.append("dependency_missing")
    if dependency_state["incomplete_step_ids"]:
        reasons.append("dependency_incomplete")
    if active_runs:
        reasons.append("active_run_exists")
    dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
    blocked_reason = dependencies.get("blocked_reason")
    if isinstance(blocked_reason, str) and blocked_reason:
        reasons.append(f"scheduler:{blocked_reason}")
    return reasons


def assignment_status(step: TaskStep, agent: AgentProfile | None) -> str:
    if step.assigned_agent_profile_id is None:
        return "unassigned"
    if agent is None:
        return "missing_agent"
    if agent.status != "active":
        return "inactive_agent"
    return "assigned"


def agent_payload(agent: AgentProfile | None) -> dict[str, object] | None:
    if agent is None:
        return None
    return {
        "id": agent.id,
        "name": agent.name,
        "role": agent.role,
        "status": agent.status,
    }


def run_payload(run: AgentRun) -> dict[str, object]:
    return {
        "id": run.id,
        "status": run.status,
        "agent_profile_id": run.agent_profile_id,
        "runtime_id": run.runtime_id,
        "runtime_space_id": run.runtime_space_id,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "error": redact_sensitive_payload(run.error) if isinstance(run.error, dict) else None,
    }


def uuid_list_from_dependencies(
    dependencies: dict[str, object],
    key: str,
) -> list[UUID]:
    if not isinstance(dependencies, dict):
        return []
    raw_values = dependencies.get(key)
    if not isinstance(raw_values, list):
        return []
    values: list[UUID] = []
    seen: set[UUID] = set()
    for raw_value in raw_values:
        try:
            value = UUID(str(raw_value))
        except (TypeError, ValueError):
            continue
        if value in seen:
            continue
        values.append(value)
        seen.add(value)
    return values
