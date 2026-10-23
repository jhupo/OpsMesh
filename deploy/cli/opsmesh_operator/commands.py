from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def host_environment(env: dict[str, str] | None = None) -> dict[str, str]:
    """External host programs must not load the frozen CLI's bundled shared libraries."""
    result = dict(os.environ if env is None else env)
    if getattr(sys, "frozen", False) and sys.platform == "linux":
        original = result.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            result.pop("LD_LIBRARY_PATH", None)
        else:
            result["LD_LIBRARY_PATH"] = original
    return result


def run_command(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 900,
    env: dict[str, str] | None = None,
) -> str:
    """Fixed operator-owned commands only. Never return subprocess stderr or secrets to APIs."""
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=host_environment(env),
            timeout=timeout,
            check=False,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{Path(argv[0]).name} operation timed out; inspect host state") from exc
    if result.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} operation failed (exit {result.returncode})")
    return result.stdout
