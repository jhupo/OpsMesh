from __future__ import annotations

from datetime import datetime

from backend.app.runs.models import AgentRun, RunEvent


def run_activity(run: AgentRun, latest_event: RunEvent | None) -> dict[str, object]:
    event_type = latest_event.event_type if latest_event is not None else None
    phase = activity_phase(run.status, event_type)
    return {
        "phase": phase,
        "label": activity_label(phase),
        "status": run.status,
        "latest_event_type": event_type,
        "since": activity_since(run, latest_event),
        "message": latest_event.message if latest_event is not None else None,
        "recommended_action": activity_recommended_action(phase),
    }


def activity_phase(run_status: str, event_type: str | None) -> str:
    if run_status == "queued":
        return "queued"
    if run_status == "waiting_runtime":
        return "waiting_runtime"
    if run_status == "waiting_approval":
        return "waiting_approval"
    if event_type in {"tool.waiting", "runtime.waiting", "run.waiting_runtime"}:
        return "waiting_runtime"
    if event_type == "approval.requested":
        return "waiting_approval"
    if event_type in {"tool.called", "tool.blocked"}:
        return "tool_calling"
    if event_type == "tool.failed":
        return "tool_failed"
    if event_type == "tool.completed":
        return "tool_completed"
    if event_type == "run.claimed":
        return "worker_claimed"
    if event_type == "run.context_built":
        return "context_ready"
    if event_type == "model.request_started":
        return "model_running"
    if event_type == "model.response_received":
        return "model_processing"
    if event_type == "model.request_failed":
        return "model_failed"
    if event_type in {"model_provider.fallback_selected", "model.fallback"}:
        return "model_routing"
    if event_type in {"model.usage", "agent.raw_item"}:
        return "model_running"
    if event_type == "agent.handoff":
        return "handoff"
    if event_type == "run.cancelled":
        return "cancelling"
    if run_status == "running":
        return "model_running"
    return run_status


def activity_label(phase: str) -> str:
    labels = {
        "queued": "Queued",
        "worker_claimed": "Worker claimed",
        "context_ready": "Context ready",
        "model_running": "Calling model",
        "model_processing": "Processing model result",
        "model_failed": "Model call failed",
        "model_routing": "Switching model provider",
        "tool_calling": "Calling tool",
        "tool_completed": "Processing tool result",
        "tool_failed": "Tool failed",
        "waiting_runtime": "Waiting for runtime",
        "waiting_approval": "Waiting for approval",
        "handoff": "Handing off",
        "cancelling": "Cancelling",
    }
    return labels.get(phase, phase.replace("_", " ").title())


def activity_since(run: AgentRun, latest_event: RunEvent | None) -> datetime:
    if latest_event is not None:
        return latest_event.created_at
    return run.started_at or run.created_at


def activity_recommended_action(phase: str) -> str | None:
    actions = {
        "waiting_runtime": "inspect_runtime_capacity",
        "waiting_approval": "review_pending_approval",
        "tool_failed": "inspect_tool_error",
        "queued": "monitor_worker_queue",
        "worker_claimed": "monitor_worker_startup",
        "context_ready": "monitor_model_request",
        "model_running": "monitor_model_response",
        "model_processing": "monitor_result_processing",
        "model_failed": "inspect_model_provider",
        "model_routing": "monitor_fallback_provider",
        "cancelling": "monitor_worker_cancel_request",
    }
    return actions.get(phase)
