"""Application policy contracts independent of model and bot providers."""

from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opsmesh.framework.errors import OpsMeshError


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, from_attributes=True)

    status: Literal["ok", "unknown"]
    action: Literal["allow", "review", "remove"]
    reason: str = Field(default="Policy review", max_length=200)
    reason_code: str = Field(default="POLICY_REVIEW", max_length=120)
    categories: tuple[str, ...] = ()
    probabilities: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fail_closed(self) -> "ReviewDecision":
        if self.status == "unknown" and self.action != "review":
            raise ValueError("An unknown review cannot allow or remove a message")
        return self


def conversation_text(output: object) -> str:
    """Read the Agent's final text; an empty answer leaves the channel silent."""
    if not isinstance(output, str):
        raise OpsMeshError("CONVERSATION_OUTPUT_INVALID", "Conversation output must be text")
    return output.strip()


class ConversationPolicy(Protocol):
    async def review(self, text: str, *, context: Sequence[str] = ()) -> ReviewDecision: ...
