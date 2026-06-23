from backend.app.tasks.models import TaskStep


def positive_numeric_usage(value: object) -> dict[str, int | float]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, int | float] = {}
    for key, amount in value.items():
        if not isinstance(key, str) or isinstance(amount, bool):
            continue
        if isinstance(amount, int | float) and amount > 0:
            normalized[key] = amount
            continue
        if isinstance(amount, str):
            try:
                parsed = float(amount)
            except ValueError:
                continue
            if parsed > 0:
                normalized[key] = parsed
    return normalized


def positive_int_usage(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, amount in value.items():
        if not isinstance(key, str) or isinstance(amount, bool):
            continue
        if isinstance(amount, int) and amount > 0:
            normalized[key] = amount
            continue
        if isinstance(amount, float) and amount > 0:
            normalized[key] = int(amount)
            continue
        if isinstance(amount, str):
            try:
                parsed = int(amount)
            except ValueError:
                continue
            if parsed > 0:
                normalized[key] = parsed
    return normalized


def scheduler_numeric_limits(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    result = {
        str(key): float(raw_value)
        for key, raw_value in value.items()
        if isinstance(raw_value, int | float) and not isinstance(raw_value, bool) and raw_value >= 0
    }
    return result or None


def step_resource_requirements(step: TaskStep) -> dict[str, float]:
    dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
    raw_requirements = dependencies.get("resource_requirements")
    if not isinstance(raw_requirements, dict):
        return {}
    return {
        str(key): float(raw_value)
        for key, raw_value in raw_requirements.items()
        if isinstance(raw_value, int | float) and not isinstance(raw_value, bool) and raw_value > 0
    }


def merge_usage_max(target: dict[str, int], update: dict[str, int]) -> None:
    for key, value in update.items():
        target[key] = max(target.get(key, 0), value)


def merge_workspace_slot_usage(target: dict[str, int], update: dict[str, int]) -> None:
    for key in ("docker_runtimes", "self_hosted_jobs"):
        value = update.get(key)
        if value is not None:
            target[key] = max(target.get(key, 0), value)
