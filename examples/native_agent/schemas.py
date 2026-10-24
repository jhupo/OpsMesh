from pydantic import BaseModel, ConfigDict, Field


class AgentConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str | None = None
    model: str | None = Field(default=None, min_length=1)
    instructions: str = "Answer the user's request. Ask for approval before any protected action."
    tools: list[str] = Field(default_factory=list)
    mcps: list[str] = Field(default_factory=list)
    reserve_tokens: int = Field(default=8192, ge=1)
