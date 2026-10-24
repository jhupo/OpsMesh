from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class Decision:
    request_id: str
    user_id: str
    value: Any


@dataclass(frozen=True)
class Suspended:
    checkpoint: dict[str, Any]
    prompt: str
    kind: Literal["approval", "question", "pause"] = "approval"
    recipient_id: str | None = None
    timeout_seconds: int = 3600
    details: dict[str, Any] = field(default_factory=dict)
