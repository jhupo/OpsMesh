from __future__ import annotations

from typing import Literal

from agents import ModelSettings


class OpenAIModelSettingsMapper:
    def map_settings(self, settings: dict[str, object]) -> ModelSettings:
        truncation = settings.get("truncation")
        safe_truncation: Literal["auto", "disabled"] | None = None
        if truncation == "auto" or truncation == "disabled":
            safe_truncation = truncation

        verbosity = settings.get("verbosity")
        safe_verbosity: Literal["low", "medium", "high"] | None = None
        if verbosity == "low" or verbosity == "medium" or verbosity == "high":
            safe_verbosity = verbosity

        metadata = settings.get("metadata")
        safe_metadata: dict[str, str] | None = None
        if isinstance(metadata, dict) and all(
            isinstance(key, str) and isinstance(value, str) for key, value in metadata.items()
        ):
            safe_metadata = {str(key): str(value) for key, value in metadata.items()}

        return ModelSettings(
            temperature=float_setting(settings, "temperature"),
            top_p=float_setting(settings, "top_p"),
            frequency_penalty=float_setting(settings, "frequency_penalty"),
            presence_penalty=float_setting(settings, "presence_penalty"),
            max_tokens=int_setting(settings, "max_tokens"),
            tool_choice=tool_choice_setting(settings),
            parallel_tool_calls=bool_setting(settings, "parallel_tool_calls"),
            truncation=safe_truncation,
            verbosity=safe_verbosity,
            metadata=safe_metadata,
            store=bool_setting(settings, "store"),
            include_usage=bool_setting(settings, "include_usage"),
        )


def float_setting(settings: dict[str, object], key: str) -> float | None:
    value = settings.get(key)
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def int_setting(settings: dict[str, object], key: str) -> int | None:
    value = settings.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def bool_setting(settings: dict[str, object], key: str) -> bool | None:
    value = settings.get(key)
    if isinstance(value, bool):
        return value
    return None


def tool_choice_setting(settings: dict[str, object]) -> str | None:
    value = settings.get("tool_choice")
    if not isinstance(value, str) or not value:
        return None
    if value in {"auto", "required", "none"}:
        return value
    return value if value.replace("_", "").replace("-", "").isalnum() else None
