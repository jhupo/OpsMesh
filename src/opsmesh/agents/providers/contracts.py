from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import TypeAdapter, ValidationError


@dataclass(frozen=True)
class ResolvedModelProvider:
    provider: str | None
    model: str
    base_url: str | None
    api_key: str | None
    model_api: str | None
    credential_id: UUID | None


class ModelProviderUnavailableError(ValueError):
    pass


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
