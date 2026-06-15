import json

from backend.app.agent_runtime.contracts import AgentRunResult


def coerce_agent_run_result(result: AgentRunResult | str) -> AgentRunResult:
    if isinstance(result, AgentRunResult):
        return result
    return AgentRunResult(
        final_output=result,
        raw_output=json_object_from_text(result),
    )


def run_output_payload(result: AgentRunResult) -> dict[str, object]:
    payload: dict[str, object] = {"final_output": result.final_output}
    if result.raw_output is not None:
        payload["raw_output"] = json_safe_object(result.raw_output)
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
    if isinstance(value, dict):
        return {str(key): json_safe_object(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe_object(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return json_safe_object(model_dump(mode="json"))
    return str(value)
