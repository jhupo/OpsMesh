"""QQ official bot credentials; no implicit personal-account login."""

from pydantic import BaseModel, ConfigDict, Field


class QQConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    app_secret: str = Field(min_length=1, repr=False)
    sandbox: bool = False
    intents: int = Field(default=(1 << 25) | (1 << 30) | (1 << 12), ge=0)
