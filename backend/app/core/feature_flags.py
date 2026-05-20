from collections.abc import Mapping
from dataclasses import dataclass

from backend.app.core.config import Settings, get_settings

DEFAULT_FEATURE_FLAGS: dict[str, bool] = {
    "docker_runtimes": True,
    "mcp_tools": True,
    "operations_aggregates": True,
    "real_openai_runner": False,
    "runtime_shell": True,
    "self_hosted_runtimes": True,
    "talent_marketplace": True,
    "workspace_memory": False,
}

_FEATURE_FLAG_SETTINGS_KEY = "feature_flags"


@dataclass(frozen=True)
class FeatureFlagDecision:
    key: str
    enabled: bool
    source: str
    reason: str


class FeatureFlagService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        workspace_settings: Mapping[str, object] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._workspace_flags = _extract_flag_map(workspace_settings)

    def is_enabled(self, key: str) -> bool:
        return self.decision(key).enabled

    def decision(self, key: str) -> FeatureFlagDecision:
        normalized_key = normalize_feature_flag_key(key)

        workspace_value = self._workspace_flags.get(normalized_key)
        if workspace_value is not None:
            return FeatureFlagDecision(
                key=normalized_key,
                enabled=workspace_value,
                source="workspace",
                reason="workspace override",
            )

        global_value = self._settings.feature_flags.get(normalized_key)
        if isinstance(global_value, bool):
            return FeatureFlagDecision(
                key=normalized_key,
                enabled=global_value,
                source="settings",
                reason="global setting",
            )

        default_value = DEFAULT_FEATURE_FLAGS.get(normalized_key)
        if default_value is not None:
            return FeatureFlagDecision(
                key=normalized_key,
                enabled=default_value,
                source="default",
                reason="known default",
            )

        return FeatureFlagDecision(
            key=normalized_key,
            enabled=False,
            source="default",
            reason="unknown flag defaults disabled",
        )


def normalize_feature_flag_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


def get_feature_flags(
    *,
    settings: Settings | None = None,
    workspace_settings: Mapping[str, object] | None = None,
) -> FeatureFlagService:
    return FeatureFlagService(settings=settings, workspace_settings=workspace_settings)


def enabled_feature_flags(
    *,
    settings: Settings | None = None,
    workspace_settings: Mapping[str, object] | None = None,
) -> list[str]:
    service = get_feature_flags(settings=settings, workspace_settings=workspace_settings)
    keys = set(DEFAULT_FEATURE_FLAGS)
    if settings is not None:
        keys.update(normalize_feature_flag_key(key) for key in settings.feature_flags)
    keys.update(_extract_flag_map(workspace_settings))
    return sorted(key for key in keys if service.is_enabled(key))


def _extract_flag_map(raw_settings: Mapping[str, object] | None) -> dict[str, bool]:
    if raw_settings is None:
        return {}
    raw_flags = raw_settings.get(_FEATURE_FLAG_SETTINGS_KEY)
    if not isinstance(raw_flags, Mapping):
        return {}
    return {
        normalize_feature_flag_key(str(key)): value
        for key, value in raw_flags.items()
        if isinstance(value, bool)
    }
