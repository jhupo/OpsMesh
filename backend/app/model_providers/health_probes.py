from typing import Literal

from pydantic import TypeAdapter, ValidationError

ProviderProbeName = Literal["models", "inference"]
_PROBES = TypeAdapter(tuple[ProviderProbeName, ...])


def provider_health_probes(value: object) -> tuple[ProviderProbeName, ...]:
    if value is None:
        return ("models", "inference")
    if not isinstance(value, list | tuple):
        raise ValueError("probes must be a list")
    try:
        probes = _PROBES.validate_python(value)
    except ValidationError as exc:
        raise ValueError("Unsupported provider health probe; expected models or inference") from exc
    if not probes:
        raise ValueError("probes must include at least one probe")
    return tuple(dict.fromkeys(probes))
