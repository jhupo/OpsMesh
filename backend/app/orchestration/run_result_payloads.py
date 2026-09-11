import json
from dataclasses import asdict
from uuid import UUID

from backend.app.agent_runtime.core.contracts import AgentRunResult


def run_output_payload(result: AgentRunResult) -> dict[str, object]:
    payload: dict[str, object] = {"final_output": result.final_output}
    if result.raw_output is not None:
        payload["raw_output"] = json_safe_object(result.raw_output)
    if result.structured_output is not None:
        payload["structured_output"] = json_safe_object(asdict(result.structured_output))
    if result.handoffs:
        payload["handoffs"] = json_safe_object([asdict(item) for item in result.handoffs])
    if result.agent_tool_calls:
        payload["agent_tool_calls"] = json_safe_object(
            [asdict(item) for item in result.agent_tool_calls]
        )
    if result.guardrail_results:
        payload["guardrail_results"] = json_safe_object(
            [asdict(item) for item in result.guardrail_results]
        )
    if result.usage is not None:
        payload["usage"] = json_safe_object(asdict(result.usage))
    return payload


def json_object_from_text(value: str) -> dict[str, object] | None:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def json_safe_object(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe_object(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe_object(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return json_safe_object(model_dump(mode="json"))
    return str(value)
