"""Opt-in destructive integration test for a dedicated systemd CI machine."""

import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux"
    or os.environ.get("OPSMESH_DISPOSABLE_SYSTEMD_TEST") != "1",
    reason="disposable systemd host required",
)


def test_installed_unit_lifecycle_and_restore(tmp_path):
    exe = "/usr/local/bin/opsmesh"

    def run(*args, ok=True):
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=150
        )
        if ok:
            assert result.returncode == 0, (args, result.stdout, result.stderr)
        return result

    def cli(*args):
        return json.loads(run("sudo", exe, "--json", *args).stdout)["result"]

    def maintenance(*args):
        return json.loads(
            run(
                "sudo", "-u", "opsmesh", exe, "--json", "maintenance", *args
            ).stdout
        )["result"]

    fixture = runpy.run_path(
        str(
            Path(__file__).resolve().parents[1]
            / "unit"
            / "test_cli_management.py"
        )
    )["wheel"]

    def publish(ver, broken=False):
        args = fixture(tmp_path, ver, broken=broken)
        run(
            "sudo",
            "install",
            "-m",
            "0644",
            str(tmp_path / args["wheel"]),
            "/var/lib/opsmesh-inbox/",
        )
        return args["wheel"], "--sha256", args["sha256"]

    assert cli("plugin", "list") == []
    assert run(exe, "plugin", "list", ok=False).returncode == 4
    # The administrator permits a supervised upgrade for this disposable plugin.
    run(
        "sudo",
        "sed",
        "-i",
        's/^restart_safe = .*/restart_safe = ["probe"]/',
        "/etc/opsmesh/opsmesh.toml",
    )
    run("sudo", "systemctl", "restart", "opsmesh.service")
    cli("plugin", "install", *publish("1"))
    cli("plugin", "enable", "probe", "--now", "--allow-restart")
    assert cli("plugin", "status", "probe")["running_version"] == "1"
    instance = cli("service", "status")["instance"]
    cli("plugin", "upgrade", "probe", *publish("2"), "--allow-restart")
    assert cli("service", "status")["instance"] != instance
    bad = run(
        "sudo",
        exe,
        "plugin",
        "upgrade",
        "probe",
        *publish("3", True),
        "--allow-restart",
        ok=False,
    )
    assert bad.returncode == 1, bad
    assert cli("plugin", "status", "probe")["running_version"] == "2"
    payload = tmp_path / "payload"
    payload.write_text("kept")
    local_file = "/var/lib/opsmesh/plugins/probe/local.toml"
    run(
        "sudo",
        "install",
        "-o",
        "opsmesh",
        "-m",
        "0600",
        str(payload),
        local_file,
    )
    run("sudo", "systemctl", "stop", "opsmesh.service")
    snapshot = maintenance("backup")["snapshot"]
    maintenance("check", "--snapshot", snapshot)
    run("sudo", "systemctl", "start", "opsmesh.service")
    cli("plugin", "disable", "probe", "--now", "--allow-restart")
    cli("plugin", "uninstall", "probe")
    cli("plugin", "delete", "probe", "--confirm", "probe")
    run("sudo", "systemctl", "stop", "opsmesh.service")
    maintenance("restore", snapshot, "--confirm", snapshot)
    run("sudo", "systemctl", "start", "opsmesh.service")
    assert run("sudo", "cat", local_file).stdout == "kept"
    assert cli("plugin", "status", "probe")["running_version"] == "2"
    run("sudo", "logrotate", "-f", "/etc/logrotate.d/opsmesh")
    cli("plugin", "disable", "probe")
    assert "Completed" in run("sudo", exe, "service", "logs").stdout
