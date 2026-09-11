from __future__ import annotations

from typing import Any

from agents.sandbox import Manifest, SandboxRunConfig

from backend.app.agent_runtime.sandbox.contracts import SandboxManifest


def manifest_to_openai_payload(manifest: SandboxManifest) -> dict[str, Any]:
    """Translate the stable OpsMesh manifest to the OpenAI SDK manifest shape."""
    return {
        "root": manifest.root,
        "environment": dict(manifest.environment),
        "files": list(manifest.files),
    }


def manifest_to_openai_run_config(manifest: SandboxManifest) -> SandboxRunConfig:
    """Build the official OpenAI SDK sandbox configuration from an OpsMesh manifest."""
    return SandboxRunConfig(
        manifest=Manifest(
            root=manifest.root,
        )
    )


def sandbox_settings_for_claude(*, network_disabled: bool) -> dict[str, Any]:
    """Translate product network policy to Claude Agent SDK sandbox settings."""
    settings: dict[str, Any] = {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
    }
    if network_disabled:
        settings["network"] = {"allowedDomains": [], "deniedDomains": ["*"]}
    return settings


def sandbox_is_required(execution_mode: str) -> bool:
    return execution_mode != "none"
