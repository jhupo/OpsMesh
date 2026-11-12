"""Configuration owned by the example conversation plugin."""

import os

from openai.types.shared import Reasoning
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from . import channels


class ChatSettings(BaseModel):
    """Declare model and optional MCP connection settings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str = Field(min_length=1)
    reasoning: Reasoning | None = None
    name: str = "OpsMesh Chat Agent"
    instructions: str = "Answer the user's question clearly and concisely."
    max_turns: int = Field(default=8, ge=1)
    mcp_url: HttpUrl | None = None
    mcp_token_ref: str | None = None
    qq: channels.qq.QQConnectionSettings | None = None
    dingtalk: channels.dingtalk.DingTalkConnectionSettings | None = None


def secret(reference: str) -> str:
    """Read one deployment secret without including its value in errors."""
    value = os.environ.get(reference)
    if not value:
        raise ValueError(
            f"Missing credential environment variable: {reference}"
        )
    return value
