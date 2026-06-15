import json
from hashlib import sha256

from backend.app.agent_runtime.contracts import AgentRunRequest
from backend.app.runs.models import AgentRun
from backend.app.tasks.models import Task


def model_request_review_context(request: AgentRunRequest) -> dict[str, object]:
    return {
        "agent_profile_id": str(request.agent_profile.id)
        if request.agent_profile.id is not None
        else None,
        "agent_role": request.agent_profile.role,
        "task_id": str(request.context.task_id) if request.context.task_id is not None else None,
        "run_id": str(request.context.run_id),
        "model": request.model,
        "provider": request.provider,
        "model_api": request.model_api,
        "model_provider_credential_id": str(request.model_provider_credential_id)
        if request.model_provider_credential_id is not None
        else None,
        "allowed_tools": list(request.context.allowed_tools),
    }


def model_request_review_input(
    run: AgentRun,
    task: Task | None,
    request: AgentRunRequest,
) -> str:
    parts: list[str] = []
    if task is not None:
        parts.append(f"Task title: {task.title}")
        if task.description:
            parts.append(f"Task description: {task.description}")
        if task.input:
            parts.append(f"Task input: {task.input}")
    if run.input:
        parts.append(f"Run input: {run.input}")
    if parts:
        return "\n\n".join(parts)
    return request.input_text


def model_request_review_fingerprint(request: AgentRunRequest, input_text: str) -> str:
    payload = {
        "agent_profile_id": str(request.agent_profile.id)
        if request.agent_profile.id is not None
        else None,
        "input_sha256": sha256(input_text.encode("utf-8")).hexdigest(),
        "model": request.model,
        "model_api": request.model_api,
        "provider": request.provider,
        "model_provider_credential_id": str(request.model_provider_credential_id)
        if request.model_provider_credential_id is not None
        else None,
        "allowed_tools": sorted(request.context.allowed_tools),
        "continuation_count": len(request.continuations),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def model_provider_request_snapshot(request: AgentRunRequest) -> dict[str, object]:
    return {
        "provider": request.provider,
        "model": request.model,
        "model_api": request.model_api,
        "credential_id": str(request.model_provider_credential_id)
        if request.model_provider_credential_id is not None
        else None,
    }


def model_provider_fallback_policy(settings: dict[str, object]) -> dict[str, object] | None:
    raw = settings.get("model_provider_fallback")
    if not isinstance(raw, dict):
        return None
    if raw.get("enabled") is not True:
        return None
    return raw
