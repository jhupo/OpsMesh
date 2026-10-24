from typing import Literal

from nonebot.adapters.qq.config import Intents
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class QQConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(min_length=1, max_length=100)
    secret: SecretStr = Field(min_length=1)
    transport: Literal["websocket", "webhook"] = "websocket"
    intents: Intents = Field(
        default_factory=lambda: Intents(
            c2c_group_at_messages=True, direct_message=True, interaction=True
        )
    )
    shard: tuple[int, int] | None = None
    webhook_host: str = "127.0.0.1"
    webhook_port: int = Field(default=8081, ge=1, le=65535)

    @model_validator(mode="after")
    def validate_shard(self) -> "QQConfig":
        if self.shard is not None and not 0 <= self.shard[0] < self.shard[1]:
            raise ValueError("Shard must satisfy 0 <= index < count")
        return self
