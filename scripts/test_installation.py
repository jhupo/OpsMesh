"""Exercise the real installer on a disposable GitHub-hosted Linux runner."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*arguments: str, success: bool = True) -> subprocess.CompletedProcess:
    """Run a bounded installer or service operation."""
    result = subprocess.run(arguments, timeout=180, check=False)
    if (result.returncode == 0) != success:
        subprocess.run(
            ["sudo", "journalctl", "-u", "opsmesh", "-n", "100"], check=False
        )
        raise AssertionError(f"Unexpected exit status: {result.returncode}")
    return result


def changed_archive(
    archive: Path, directory: Path, *, fail_start: bool = False
) -> tuple[Path, Path]:
    """Add an inert marker to exercise upgrade with a different content hash."""
    changed = directory / archive.name
    with (
        tarfile.open(archive) as source,
        tarfile.open(changed, "w:gz") as target,
    ):
        for member in source.getmembers():
            if fail_start and member.name.endswith(
                "/deploy/systemd/opsmesh.service"
            ):
                content = (
                    b"[Service]\nType=notify\nExecStart=/usr/bin/false\n"
                    b"TimeoutStartSec=2\n"
                    b"[Install]\nWantedBy=multi-user.target\n"
                )
                member.size = len(content)
                target.addfile(member, io.BytesIO(content))
                continue
            target.addfile(
                member, source.extractfile(member) if member.isfile() else None
            )
        marker = tarfile.TarInfo(f"{archive.name[:-7]}/upgrade-marker")
        marker.size = 7
        target.addfile(marker, io.BytesIO(b"upgrade"))
    checksum = directory / "SHA256SUMS"
    with changed.open("rb") as content:
        digest = hashlib.file_digest(content, "sha256").hexdigest()
    checksum.write_text(f"{digest}  {changed.name}\n")
    return changed, checksum


def main() -> None:
    """Validate installation, failed upgrades, and binary rollback."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Run only on a disposable GitHub Actions runner")
    directory = Path(sys.argv[1]).resolve()
    (archive,) = (
        path
        for path in directory.glob("opsmesh-*.tar.gz")
        if re.fullmatch(
            r"opsmesh-\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?-linux-(?:x86_64|aarch64)\.tar\.gz",
            path.name,
        )
    )
    checksums = archive.with_name(
        archive.name.removesuffix(".tar.gz") + ".sha256"
    )
    installer = str(ROOT / "deploy/install.sh")
    with tempfile.TemporaryDirectory() as name:
        temporary = Path(name)
        manifest = json.loads((ROOT / "deploy/host.example.json").read_text())
        assert manifest["plugins"] == []
        manifest["environment"] = "installation-test"
        config = temporary / "host.json"
        config.write_text(json.dumps(manifest))
        environment = temporary / "opsmesh.env"
        environment.write_text("OPSMESH_INSTALL_TEST=preserve-value\n")
        command = ["sudo", "bash", installer]
        broken_directory = temporary / "broken"
        broken_directory.mkdir()
        broken, broken_sum = changed_archive(
            archive, broken_directory, fail_start=True
        )
        run(
            *command,
            "--archive",
            str(broken),
            "--checksums",
            str(broken_sum),
            success=False,
        )
        assert not Path("/opt/opsmesh/current").exists()
        assert not Path("/etc/systemd/system/opsmesh.service").exists()
        run(
            *command,
            "--archive",
            str(archive),
            "--checksums",
            str(checksums),
        )
        installed = json.loads(
            subprocess.check_output(
                ["sudo", "cat", "/etc/opsmesh/host.json"], text=True, timeout=30
            )
        )
        assert installed["plugins"] == []
        run("systemctl", "is-active", "--quiet", "opsmesh")
        run(
            "sudo",
            "systemd-analyze",
            "verify",
            "/etc/systemd/system/opsmesh.service",
        )
        run("sudo", "touch", "/var/lib/opsmesh/preserve-me")
        original = Path("/opt/opsmesh/current").resolve()
        changed, changed_sum = changed_archive(archive, temporary)
        run(
            *command,
            "--archive",
            str(changed),
            "--checksums",
            str(changed_sum),
            "--manifest",
            str(config),
            "--env-file",
            str(environment),
        )
        assert Path("/opt/opsmesh/current").resolve() != original
        run(*command, "--rollback")
        assert Path("/opt/opsmesh/current").resolve() == original
        run("sudo", "test", "-f", "/var/lib/opsmesh/preserve-me")
        run("sudo", "cmp", str(environment), "/etc/opsmesh/opsmesh.env")
        run("sudo", "cmp", str(config), "/etc/opsmesh/host.json")
        config.write_text('{"plugins": "invalid"}')
        run(
            *command,
            "--archive",
            str(changed),
            "--checksums",
            str(changed_sum),
            "--manifest",
            str(config),
            success=False,
        )
        run("systemctl", "is-active", "--quiet", "opsmesh")
        assert Path("/opt/opsmesh/current").resolve() == original
        installed = json.loads(
            subprocess.check_output(
                ["sudo", "cat", "/etc/opsmesh/host.json"], text=True, timeout=30
            )
        )
        assert installed["environment"] == "installation-test"
        run("sudo", "systemctl", "stop", "opsmesh")
    print("Systemd installation checks passed")


if __name__ == "__main__":
    main()
