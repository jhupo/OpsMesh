"""Read opaque protocol sections from the channels configuration."""

from importlib import import_module
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from opsmesh.plugins.config import get_plugin_config


class ChannelsSettings(BaseModel):
    """Keep protocol sections unparsed until a declared entrance needs them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    api: Any = None
    qq: Any = None
    dingtalk: Any = None


def protocol_settings(name: str) -> Any:
    """Return one section; its protocol owns validation and defaults."""
    settings = get_plugin_config(ChannelsSettings)
    return getattr(settings, name)


class ChannelConnections(BaseModel):
    """Named robot accounts belonging to one consuming plugin."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True
    )

    qq: dict[str, SerializeAsAny[BaseModel]] = Field(default_factory=dict)
    dingtalk: dict[str, SerializeAsAny[BaseModel]] = Field(default_factory=dict)

    @field_validator("qq", "dingtalk", mode="before")
    @classmethod
    def validate_accounts(
        cls, values: dict, info: ValidationInfo
    ) -> dict[str, BaseModel]:
        """Validate only protocols requested by this consumer."""
        if not isinstance(values, dict):
            raise ValueError(
                "Connections must be a mapping of names to settings"
            )
        if not values:
            return values
        from ._validation import connection_segment

        protocol = info.field_name
        module = import_module(f"{__package__}.{protocol}.config")
        schema = (
            module.QQConnectionSettings
            if protocol == "qq"
            else module.DingTalkConnectionSettings
        )
        accounts: set[str] = set()
        parsed = {}
        for name, raw in values.items():
            connection_segment(name, "Connection name")
            try:
                settings = schema.model_validate(raw)
            except ValidationError as error:
                fields = ", ".join(
                    ".".join(str(part) for part in item["loc"])
                    for item in error.errors(include_input=False)
                )
                raise ValueError(
                    f"{protocol}.{name}: invalid {fields}"
                ) from None
            account = (
                settings.app_id if protocol == "qq" else settings.client_id
            )
            if account in accounts:
                raise ValueError(
                    f"{protocol}.{name}: robot account is already configured"
                )
            accounts.add(account)
            parsed[name] = settings
        return parsed
