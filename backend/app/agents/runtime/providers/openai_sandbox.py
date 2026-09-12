"""Map product workspace declarations only at the OpenAI SDK boundary."""

from agents.sandbox import Manifest, SandboxRunConfig

from backend.app.agents.runtime.runtime.contracts import SandboxManifest


def sandbox_run_config(manifest: SandboxManifest) -> SandboxRunConfig:
    return SandboxRunConfig(manifest=Manifest(root=manifest.root))
