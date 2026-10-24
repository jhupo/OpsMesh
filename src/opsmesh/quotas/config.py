from dataclasses import dataclass, field
from typing import Literal

from opsmesh.shared.schemas import ReasoningEffort


@dataclass(frozen=True)
class ModelDefaults:
    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = field(default=None, repr=False)
    name: str | None = None
    api_mode: Literal["responses", "chat_completions"] = "responses"
    reasoning_effort: ReasoningEffort = "medium"
