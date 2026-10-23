"""DingTalk application bot configuration."""

from pydantic import BaseModel, ConfigDict, Field


class DingTalkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_key: str = Field(min_length=1)
    app_secret: str = Field(min_length=1, repr=False)
    robot_code: str = Field(min_length=1)
    corp_id: str = Field(min_length=1)
