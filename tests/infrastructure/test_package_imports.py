"""Fresh processes expose composition cycles hidden by pytest's shared import cache."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "entry_module",
    [
        "opsmesh.infrastructure.messaging.outbox",
        "opsmesh.infrastructure.run_workers.kubernetes.configuration",
        "opsmesh.entrypoints.cli",
    ],
)
def test_infrastructure_and_cli_can_be_imported_before_application(entry_module: str) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import importlib, sys; importlib.import_module(sys.argv[1]); "
            "from opsmesh.bootstrap.application import OpsMeshApplication",
            entry_module,
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
