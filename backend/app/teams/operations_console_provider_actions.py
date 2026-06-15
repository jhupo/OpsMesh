from __future__ import annotations


def _provider_management_suggested_actions(
    agent_bindings: list[dict[str, object]],
) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    for index, binding in enumerate(agent_bindings):
        status = binding.get("readiness_status")
        if status not in {"blocked", "degraded"}:
            continue
        actions.append(
            {
                "source": "provider_management",
                "source_index": index,
                "automation": "team_runtime_control",
                "action": "review_model_provider",
                "priority": 100 if status == "blocked" else 60,
                "reason": f"agent_model_provider_{status}",
                "agent_profile_id": binding.get("agent_profile_id"),
                "team_member_id": binding.get("team_member_id"),
                "credential_id": binding.get("credential_id"),
                "provider": binding.get("provider"),
                "selected_model": binding.get("selected_model"),
                "model_api": binding.get("model_api"),
                "reasons": binding.get("reasons", []),
                "warnings": binding.get("warnings", []),
                "available_credential_ids": binding.get("available_credential_ids", []),
                "task_ids": [],
                "task_step_ids": [],
            }
        )
    return actions
