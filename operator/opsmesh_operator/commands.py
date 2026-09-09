from __future__ import annotations

import os
import subprocess
from pathlib import Path


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
            env=env if env is not None else os.environ.copy(),
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
