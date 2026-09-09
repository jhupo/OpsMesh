from uuid import uuid4

from backend.app.teams.execution_loop_payloads import _without_finalizable_review_actions


def test_review_action_keeps_only_tasks_that_still_need_review() -> None:
    ready, pending = uuid4(), uuid4()
    command_center: dict[str, object] = {
        "summary": {"action_plan_count": 1},
        "action_plan": [{
            "action": "request_manager_review", "source": "execution_overview",
            "task_ids": [ready, pending], "task_step_ids": [], "count": 2,
        }],
    }
    result = _without_finalizable_review_actions(
        command_center,
        {"results": [{"task_id": ready, "status": "would_finalize"}]},
    )
    assert result["action_plan"] == [{
        "action": "request_manager_review", "source": "execution_overview",
        "task_ids": [pending], "task_step_ids": [], "count": 1,
    }]
    assert result["summary"] == {
        "action_plan_count": 1,
        "action_plan_source_counts": {"execution_overview": 1},
        "suppressed_finalization_action_count": 0,
    }
    assert command_center["action_plan"] != result["action_plan"]
