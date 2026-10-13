"""Channel configuration is data; secrets have a separate write-only boundary."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from opsmesh.shared.contracts import TimestampedContract


class ChannelConnectionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    account_id: str = Field(min_length=1, max_length=160)
    endpoint_url: str = Field(min_length=1, max_length=2048)
    configuration: dict[str, object] = Field(default_factory=dict)


class ChannelConnectionCreateRequest(ChannelConnectionSettings):
    install_id: UUID
    capability_key: str = Field(min_length=1, max_length=120)
    signing_secret: SecretStr = Field(min_length=32, max_length=256)


class ChannelConnectionUpdateRequest(ChannelConnectionSettings):
    expected_revision: int = Field(ge=1)
    status: Literal["active", "disabled"]
    signing_secret: SecretStr | None = Field(default=None, min_length=32, max_length=256)


class ChannelConnectionResponse(TimestampedContract):
    workspace_id: UUID
    install_id: UUID
    capability_key: str
    name: str
    account_id: str
    endpoint_url: str
    configuration: dict[str, object]
    configuration_revision: int
    status: str
