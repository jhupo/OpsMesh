"""Provider-neutral bot connection configuration and observable connection state."""

import re
from typing import Literal

from pydantic import Field, field_validator

from opsmesh.framework.typing import JsonData, OpsMeshContract


class BotConnectionConfiguration(OpsMeshContract):
    id: str = Field(min_length=1, max_length=100)
    adapter: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    self_id: str = Field(min_length=1, max_length=256)
    settings: JsonData = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    revision: int = Field(default=1, ge=1)

    @field_validator("credentials")
    @classmethod
    def credential_references(cls, value: dict[str, str]) -> dict[str, str]:
        for name, reference in value.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise ValueError("Credential names must be lower-case identifiers")
            if not re.fullmatch(r"env:[A-Za-z_][A-Za-z0-9_]*", reference):
                raise ValueError("Bot credentials require deployed environment references")
        return value


class BotAdapterConnectionStatus(OpsMeshContract):
    transport: str
    state: Literal["disabled", "configured", "connected", "connecting", "disconnected"]
    pending_calls: int = Field(default=0, ge=0)


class BotAdapterDescriptor(OpsMeshContract):
    name: str
    display_name: str
    capabilities: frozenset[str]
    settings_schema: JsonData
    credential_names: frozenset[str]
