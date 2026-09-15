from backend.app.core.config import Settings
from backend.app.core.feature_flags import (
    FeatureFlagService,
    enabled_feature_flags,
    normalize_feature_flag_key,
)


def test_known_feature_flags_use_safe_defaults() -> None:
    flags = FeatureFlagService(settings=Settings())

    docker_decision = flags.decision("docker_runtimes")
    memory_decision = flags.decision("workspace_memory")

    assert docker_decision.enabled is True
    assert docker_decision.source == "default"
    assert memory_decision.enabled is False
    assert memory_decision.source == "default"


def test_unknown_feature_flags_default_disabled() -> None:
    flags = FeatureFlagService(settings=Settings())

    decision = flags.decision("experimental-superpower")

    assert decision.key == "experimental_superpower"
    assert decision.enabled is False
    assert decision.reason == "unknown flag defaults disabled"


def test_global_feature_flag_overrides_defaults() -> None:
    settings = Settings(feature_flags={"workspace_memory": True, "docker_runtimes": False})
    flags = FeatureFlagService(settings=settings)

    assert flags.decision("workspace_memory").enabled is True
    assert flags.decision("workspace_memory").source == "settings"
    assert flags.decision("docker-runtimes").enabled is False
    assert flags.decision("docker-runtimes").source == "settings"


def test_workspace_feature_flags_override_global_settings() -> None:
    settings = Settings(feature_flags={"workspace_memory": False, "mcp_tools": True})
    flags = FeatureFlagService(
        settings=settings,
        workspace_settings={
            "feature_flags": {
                "workspace-memory": True,
                "mcp_tools": False,
            }
        },
    )

    assert flags.decision("workspace_memory").enabled is True
    assert flags.decision("workspace_memory").source == "workspace"
    assert flags.decision("mcp_tools").enabled is False
    assert flags.decision("mcp_tools").source == "workspace"


def test_non_boolean_feature_flag_values_are_ignored() -> None:
    settings = Settings(feature_flags={"workspace_memory": True})
    flags = FeatureFlagService(
        settings=settings,
        workspace_settings={
            "feature_flags": {
                "workspace_memory": "false",
                "mcp_tools": 1,
                "docker_runtimes": True,
            }
        },
    )

    assert flags.decision("workspace_memory").enabled is True
    assert flags.decision("workspace_memory").source == "settings"
    assert flags.decision("mcp_tools").enabled is True
    assert flags.decision("mcp_tools").source == "default"
    assert flags.decision("docker_runtimes").enabled is True
    assert flags.decision("docker_runtimes").source == "workspace"


def test_enabled_feature_flags_include_workspace_and_global_overrides() -> None:
    settings = Settings(feature_flags={"workspace_memory": True, "docker_runtimes": False})

    enabled = enabled_feature_flags(
        settings=settings,
        workspace_settings={"feature_flags": {"custom_tooling": True}},
    )

    assert "custom_tooling" in enabled
    assert "workspace_memory" in enabled
    assert "docker_runtimes" not in enabled


def test_normalize_feature_flag_key_is_stable_for_config_sources() -> None:
    assert normalize_feature_flag_key(" Workspace-Memory ") == "workspace_memory"
