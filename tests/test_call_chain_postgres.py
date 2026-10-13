"""HTTP, committed Outbox, native Worker, audit and logs share the SDK context."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL") or os.getenv("OPSMESH_TEST_TEMPORAL") != "1",
    reason="Real PostgreSQL and Temporal required",
)


@pytest.mark.parametrize("flags", ["00", "01"])
def test_call_chain_survives_disabled_export_and_concurrent_workspaces(flags: str) -> None:
    # The public OTel API has one immutable global provider; isolate startup settings.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from tests.fixtures.audit_chain import verify; verify({flags!r})",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
