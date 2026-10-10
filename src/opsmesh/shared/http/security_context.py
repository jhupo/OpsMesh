from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SecurityRequestContext:
    source_ip: str | None
    user_agent: str | None
    request_id: str | None
    path: str
    method: str
