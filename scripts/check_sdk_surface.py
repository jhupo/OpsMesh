"""Check required public SDK types without API calls or sandbox creation."""

from importlib.metadata import version

from agents import Agent, Runner, RunState
from agents.sandbox import (
    Manifest,
    MemoryGenerateConfig,
    MemoryLayoutConfig,
    MemoryReadConfig,
    SandboxAgent,
    SandboxRunConfig,
)
from agents.sandbox.capabilities import Filesystem, Memory, Shell, Skills

PUBLIC_TYPES = (
    Agent,
    Runner,
    RunState,
    SandboxAgent,
    SandboxRunConfig,
    Manifest,
    Memory,
    MemoryReadConfig,
    MemoryGenerateConfig,
    MemoryLayoutConfig,
    Shell,
    Filesystem,
    Skills,
)


def main() -> int:
    print(f"openai-agents {version('openai-agents')}: {len(PUBLIC_TYPES)} public types imported.")
    print("Import surface only; no model calls, sandbox creation or behavioral validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
