from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class DingTalkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1, max_length=200)
    client_secret: SecretStr = Field(min_length=1)
    robot_code: str | None = None
    subscribe_events: bool = True
    card_callbacks: bool = True
    graph_callbacks: bool = True
    card_callback_response: dict[str, Any] = Field(default_factory=dict)
