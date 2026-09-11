"""Map the authorized network policy to Claude's sandbox configuration."""

from claude_agent_sdk.types import SandboxSettings


def sandbox_settings_for_claude(*, network_disabled: bool) -> SandboxSettings:
    settings: SandboxSettings = {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
    }
    if network_disabled:
        settings["network"] = {"allowedDomains": [], "deniedDomains": ["*"]}
    return settings
