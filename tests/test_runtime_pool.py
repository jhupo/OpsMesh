"""Opt-in real Linux process and identity acceptance; never invokes business APIs."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_RUNTIME_IMAGE"), reason="Linux Runtime image required"
)
def test_preloaded_runtime_pool_isolates_capacity_cancellation_and_sdk_resume():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "NET_ADMIN",
            "--cap-add",
            "KILL",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "DAC_OVERRIDE",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "768m",
            "--pids-limit",
            "128",
            "--volume",
            f"{root}:/validation:ro",
            "--entrypoint",
            "python",
            os.environ["OPSMESH_TEST_RUNTIME_IMAGE"],
            "/validation/tests/fixtures/runtime_pool_probe.py",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
