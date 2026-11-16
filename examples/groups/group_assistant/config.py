"""Model settings owned by this example, independent of its channels."""

from pydantic import BaseModel, ConfigDict, Field


class AgentSettings(BaseModel):
    """Select the model shared by the two native agent definitions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1)
