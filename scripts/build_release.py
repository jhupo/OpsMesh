"""Build a native Linux archive with an embedded Python runtime."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from release_artifacts import create_archive, runtime_dependencies

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    """Compile in a clean Linux build environment for its own architecture."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform != "linux" or platform.libc_ver()[0] != "glibc":
        raise SystemExit(
            "Build on glibc Linux; cross-compilation is not supported"
        )
    architecture = {"x86_64": "x86_64", "aarch64": "aarch64"}.get(
        platform.machine()
    )
    if architecture is None:
        raise SystemExit("Only x86_64 and aarch64 releases are supported")
    version = importlib.metadata.version("opsmesh")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            str(ROOT / "packaging" / "opsmesh.spec"),
            "--distpath",
            str(output_dir),
            "--workpath",
            str(output_dir / "work"),
        ],
        cwd=ROOT,
        check=True,
    )
    bundle = output_dir / "opsmesh"
    if not (bundle / "opsmesh").is_file():
        raise SystemExit("PyInstaller output is missing the host executable")
    for relative in (
        "install.sh",
        "host.example.json",
        "opsmesh.env.example",
        "systemd/opsmesh.service",
        "docker/Dockerfile",
        "docker/compose.yaml",
    ):
        destination = bundle / "deploy" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "deploy" / relative, destination)
    shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
    shutil.copytree(ROOT / "licenses", bundle / "licenses", dirs_exist_ok=True)
    metadata = {
        "version": version,
        "architecture": architecture,
        "python": platform.python_version(),
        "glibc": platform.libc_ver()[1],
        "dependencies": runtime_dependencies("opsmesh"),
    }
    (bundle / "BUILD.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (bundle / "GLIBC_MIN").write_text(platform.libc_ver()[1] + "\n")

    name = f"opsmesh-{version}-linux-{architecture}"
    archive = output_dir / f"{name}.tar.gz"
    create_archive(bundle, archive)
    print(archive)


if __name__ == "__main__":
    main()
