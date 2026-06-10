from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def test_real_team_http_e2e_requires_complete_provider_success_evidence() -> None:
    script = _load_real_team_http_e2e_script()

    result = script._evaluate_evidence(
        resources={"credential": {"id": "credential-1"}},
        task_id="task-1",
        task_status="completed",
        timed_out=False,
        run_items=[{"id": "run-1"}],
        run_events=[
            {
                "agent_run_id": "run-1",
                "events": [
                    {"event_type": "run.claimed"},
                    {"event_type": "model.request_started"},
                    {"event_type": "model.response_received"},
                    {"event_type": "run.completed"},
                ],
            }
        ],
        provider_usage_audit={
            "items": [
                {
                    "action": "model_provider.used",
                    "task_id": "task-1",
                    "credential_id": "credential-1",
                }
            ]
        },
    )

    assert result["passed"] is False
    assert "missing_success_run_events" in result["failures"]
    assert result["missing_success_event_types"] == ["model_provider.used"]


def test_real_team_http_e2e_accepts_complete_provider_success_evidence() -> None:
    script = _load_real_team_http_e2e_script()

    result = script._evaluate_evidence(
        resources={"credential": {"id": "credential-1"}},
        task_id="task-1",
        task_status="completed",
        timed_out=False,
        run_items=[{"id": "run-1"}],
        run_events=[
            {
                "agent_run_id": "run-1",
                "events": [
                    {"event_type": "run.claimed"},
                    {"event_type": "model.request_started"},
                    {"event_type": "model.response_received"},
                    {"event_type": "model_provider.used"},
                    {"event_type": "run.completed"},
                ],
            }
        ],
        provider_usage_audit={
            "items": [
                {
                    "action": "model_provider.used",
                    "task_id": "task-1",
                    "credential_id": "credential-1",
                }
            ]
        },
    )

    assert result["passed"] is True
    assert result["failures"] == []
    assert result["diagnosis"] == "completed"


def test_real_team_http_e2e_requires_model_response_and_completed_run_events() -> None:
    script = _load_real_team_http_e2e_script()

    result = script._evaluate_evidence(
        resources={"credential": {"id": "credential-1"}},
        task_id="task-1",
        task_status="completed",
        timed_out=False,
        run_items=[{"id": "run-1"}],
        run_events=[
            {
                "agent_run_id": "run-1",
                "events": [
                    {"event_type": "run.claimed"},
                    {"event_type": "model.request_started"},
                    {"event_type": "model_provider.used"},
                ],
            }
        ],
        provider_usage_audit={
            "items": [
                {
                    "action": "model_provider.used",
                    "task_id": "task-1",
                    "credential_id": "credential-1",
                }
            ]
        },
    )

    assert result["passed"] is False
    assert "missing_required_run_events" in result["failures"]
    assert result["missing_event_types"] == ["model.response_received", "run.completed"]


def _load_real_team_http_e2e_script() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "real-team-http-e2e.py"
    spec = importlib.util.spec_from_file_location("real_team_http_e2e", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
