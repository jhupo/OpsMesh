"""Export native Agent spans over OTLP, preserving parents and replay safety."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("OPSMESH_TEST_TEMPORAL") != "1", reason="Temporal integration opt-in required"
)


def test_native_agent_traces_reach_otlp_and_replay_does_not_duplicate() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from tests.fixtures.temporal_observability import verify; verify()",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
