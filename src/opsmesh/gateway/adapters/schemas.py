from typing import Any, Literal

from opsmesh_sdk.channels import OutgoingMessage
from pydantic import BaseModel, ConfigDict, Field


class SendMessageParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: dict[str, Any]
    text: str = ""
    content: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    reply_to: str | None = None
    scope: Literal["private", "group"] = "private"
    sequence: int = Field(default=1, ge=1)

    def message(self) -> OutgoingMessage:
        return OutgoingMessage(id="operation", conversation_id="", **self.model_dump())
