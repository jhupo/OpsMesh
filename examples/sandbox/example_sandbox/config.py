"""Settings owned by the sandbox example plugin."""

from typing import Literal

from conversation_support.identity import HTTPIdentitySettings
from openai.types.shared import Reasoning
from pydantic import ConfigDict, Field


class SandboxSettings(HTTPIdentitySettings):
    """Configure the native model without mirroring SDK sandbox options."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1)

    reasoning: Reasoning | None = None
    output_format: Literal["text", "markdown"] = "markdown"
    publication: Literal["buffered", "progressive"] = "buffered"
    max_turns: int = Field(default=8, ge=1, le=100)
