from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


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
