"""Build a Linux installation bundle containing a wheel, never a Python environment."""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_FILES = (
    "deploy/install.sh",
    "deploy/install/common.sh",
    "deploy/install/local.sh",
    "deploy/install/docker.sh",
    "deploy/install/check_ready.py",
    "deploy/systemd/opsmesh.service",
    "deploy/compose.yaml",
    "deploy/Dockerfile",
)
INSTALL_ENTRYPOINT = """#!/usr/bin/env bash
set -Eeuo pipefail
BUNDLE_DIRECTORY=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
exec bash "$BUNDLE_DIRECTORY/deploy/install.sh" "$@"
"""


def bundle_readme(version: str) -> str:
    return f"""# OpsMesh {version} — Linux installation

This distribution contains the OpsMesh wheel, a dependency list pinned with
hashes, Linux installation scripts, systemd and Docker Compose configuration.
It contains no Python interpreter, virtual environment, development checkout
or dependency wheels. Installation needs network access.

From this extracted directory, verify the download and install:

```sh
sha256sum --check SHA256SUMS
sudo bash install.sh
```

The default is a native systemd service. The installer reuses a supported local
Python 3.11-3.14 accessible to the service user, or downloads managed Python 3.13
when needed. It creates an isolated virtual environment, installs the locked
dependencies online, and installs the supplied wheel. System Python is unchanged.
Linux with systemd, Bash, curl, Git and standard GNU utilities is required.

```sh
sudo systemctl status opsmesh
sudo journalctl -u opsmesh -f
```

Configuration: `/etc/opsmesh/opsmesh.env`. Data: `/var/lib/opsmesh`.
The API listens at `http://127.0.0.1:8000` and the API documentation at `/docs`.
SQLite and a combined API/worker process are the native defaults.

Docker is explicit and requires an existing Docker Engine and Compose v2:

```sh
sudo bash install.sh --mode docker
```

This builds an application image from the wheel, then starts API, Worker,
PostgreSQL and Redis. Only the HTTP port is published. Existing Docker volumes
are preserved; do not remove them when upgrading.

The first installation prompts for an administrator password. Automated setups
can pass a single line through `--admin-password-stdin`, or use an existing
private file with `--admin-password-file PATH`. These options are exclusive.
Passwords are not stored in the long-lived service configuration.

Use `--instance NAME --port PORT` for a separate installation. The same bundle
can be installed again without resetting configuration or data. After backing
up the database and files, install a newer bundle with `--upgrade`, retaining
the original mode and instance. Schema migration stops application writers.
`--no-start` prepares and initializes without starting the application.
Run `bash install.sh --help` for all options.

The Python wheel is an installation artifact; it is not a native executable and
does not conceal Python code. Developer source archives are distributed separately.
Project: https://github.com/jhupo/OpsMesh
"""


def assemble_bundle(wheel: Path, requirements: Path, output_directory: Path) -> Path:
    """Assemble only reviewed installation files and checksum every shipped file."""
    match = re.fullmatch(r"opsmesh-(\d+\.\d+\.\d+(?:[a-z0-9.]+)?)-py3-none-any\.whl", wheel.name)
    if match is None:
        raise ValueError("Expected an OpsMesh universal Python wheel")
    version = match[1]
    with ZipFile(wheel) as package:
        metadata = package.read(f"opsmesh-{version}.dist-info/METADATA").decode()
        if f"\nVersion: {version}\n" not in "\n" + metadata:
            raise ValueError("Wheel filename and metadata versions differ")
    output_directory.mkdir(parents=True, exist_ok=True)
    archive = output_directory / f"opsmesh-{version}-linux.tar.gz"
    with tempfile.TemporaryDirectory(prefix="opsmesh-linux-build-") as temporary:
        staging = Path(temporary) / f"opsmesh-{version}-linux"
        staging.mkdir()
        for relative in (*DEPLOYMENT_FILES, "LICENSE"):
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Shell sources must also work when packaging from a Windows checkout.
            destination.write_bytes((ROOT / relative).read_bytes().replace(b"\r\n", b"\n"))
        (staging / "packages").mkdir()
        shutil.copyfile(wheel, staging / "packages" / wheel.name)
        (staging / "requirements.lock").write_bytes(
            requirements.read_bytes().replace(b"\r\n", b"\n")
        )
        (staging / "VERSION").write_text(version + "\n", encoding="utf-8", newline="\n")
        (staging / "install.sh").write_text(INSTALL_ENTRYPOINT, encoding="utf-8", newline="\n")
        (staging / "README.md").write_text(bundle_readme(version), encoding="utf-8", newline="\n")
        files = sorted(path for path in staging.rglob("*") if path.is_file())
        checksums = "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  "
            f"{path.relative_to(staging).as_posix()}\n"
            for path in files
        )
        (staging / "SHA256SUMS").write_text(checksums, encoding="utf-8", newline="\n")

        def release_metadata(member: tarfile.TarInfo) -> tarfile.TarInfo:
            member.uid = member.gid = 0
            member.uname = member.gname = "root"
            member.mode = 0o755 if member.isdir() or member.name.endswith(".sh") else 0o644
            return member

        with tarfile.open(archive, "w:gz") as bundle:
            bundle.add(staging, arcname=staging.name, filter=release_metadata)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="utf-8", newline="\n"
    )
    return archive


def build_release(output_directory: Path) -> Path:
    with tempfile.TemporaryDirectory(prefix="opsmesh-wheel-build-") as temporary:
        artifacts = Path(temporary)
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(artifacts)], cwd=ROOT, check=True
        )
        requirements = artifacts / "requirements.lock"
        subprocess.run(
            [
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--extra",
                "openai",
                "--extra",
                "s3",
                "--no-emit-project",
                "--no-annotate",
                "--no-header",
                "--output-file",
                str(requirements),
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        wheels = list(artifacts.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("Build did not produce exactly one wheel")
        return assemble_bundle(wheels[0], requirements, output_directory)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "linux")
    arguments = parser.parse_args()
    print(build_release(arguments.output_dir.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
