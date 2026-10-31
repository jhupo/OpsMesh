"""Check the installed package and user-facing command boundary."""

import subprocess
import sys
from importlib.metadata import version

from opsmesh import __version__


def test_installed_module_reports_distribution_version() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "opsmesh", "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout.strip() == f"opsmesh {version('opsmesh')}"
    assert version("opsmesh") == __version__


def test_help_succeeds_without_configuration_or_credentials() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "opsmesh", "--help"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert "--version" in result.stdout


def test_unknown_commands_fail_instead_of_claiming_to_run() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "opsmesh", "not-a-command"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 2
    assert "invalid choice" in result.stderr
