import subprocess
import sys

import pytest
from opsmesh_operator.commands import host_environment


def test_core_cli_imports_without_optional_host_dependencies():
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            """import builtins
original = builtins.__import__
def restricted(name, *args, **kwargs):
    if name == "docker" or name.startswith("docker."):
        raise ModuleNotFoundError("optional host dependency unavailable")
    return original(name, *args, **kwargs)
builtins.__import__ = restricted
from opsmesh_operator.cli import build_argument_parser
build_argument_parser().parse_args(["--help"])
""",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


@pytest.mark.parametrize("original", [None, "/host/libraries"])
def test_frozen_cli_restores_host_library_search_for_external_commands(monkeypatch, original):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    env = {"LD_LIBRARY_PATH": "/cli/_internal", "KEEP": "value"}
    if original is not None:
        env["LD_LIBRARY_PATH_ORIG"] = original
    result = host_environment(env)
    assert result.get("LD_LIBRARY_PATH") == original
    assert "LD_LIBRARY_PATH_ORIG" not in result
    assert result["KEEP"] == "value"
    assert env["LD_LIBRARY_PATH"] == "/cli/_internal"


def test_unfrozen_updater_preserves_its_host_environment(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    env = {"LD_LIBRARY_PATH": "/host/libraries"}
    assert host_environment(env) == env
    assert host_environment(env) is not env
