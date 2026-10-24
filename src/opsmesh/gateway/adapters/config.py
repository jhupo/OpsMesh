"""Validate built-in configuration without constructing a live channel."""

from typing import Any

from pydantic import SecretStr, ValidationError

from opsmesh.shared.errors import InvalidArgumentError


def validate_config(adapter: str, config: dict[str, Any]) -> dict[str, Any]:
    from .dingtalk.config import DingTalkConfig
    from .qq.config import QQConfig

    try:
        validated: Any
        if adapter == "qq":
            validated = QQConfig.model_validate(config)
        elif adapter == "dingtalk":
            validated = DingTalkConfig.model_validate(config)
        else:
            raise KeyError(adapter)
    except (KeyError, ValidationError) as exc:
        # ValidationError includes input values; never expose a secret in an error body.
        raise InvalidArgumentError("Invalid built-in channel configuration") from exc
    return {
        key: value.get_secret_value() if isinstance(value, SecretStr) else value
        for key, value in validated.model_dump(mode="python").items()
    }
