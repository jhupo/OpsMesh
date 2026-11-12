"""Settings owned by the sandbox example plugin."""

from openai.types.shared import Reasoning
from pydantic import BaseModel, ConfigDict, Field


class SandboxSettings(BaseModel):
    """Configure the native model without mirroring SDK sandbox options."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1)

    reasoning: Reasoning | None = None
    max_turns: int = Field(default=8, ge=1, le=100)
