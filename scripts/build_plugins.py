"""Build optional Linux plugin resources separately from the frozen host."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from packaging.utils import canonicalize_name
from release_artifacts import create_archive

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ("channels", "database", "execution", "filesystem")


def validate_host_runtime(host: dict) -> None:
    """Require the same native platform and ABI used for the host bundle."""
    architecture = {
        "x86_64": "x86_64",
        "aarch64": "aarch64",
        "arm64": "aarch64",
    }.get(platform.machine())
    libc, version = platform.libc_ver()
    if (
        sys.platform != "linux"
        or platform.python_version() != host["python"]
        or architecture != host["architecture"]
        or libc != "glibc"
        or version != host["glibc"]
    ):
        raise ValueError(
            "Plugin runtime must match the host's Linux architecture, "
            "Python version, and glibc build environment"
        )


def remove_host_dependencies(site: Path, dependencies: list[dict]) -> None:
    """Remove exact host-owned distributions, keeping shared namespaces."""
    supplied = {
        canonicalize_name(item["name"]): item["version"]
        for item in dependencies
    }
    for distribution in importlib.metadata.distributions(path=[str(site)]):
        name = canonicalize_name(distribution.metadata["Name"])
        if name not in supplied:
            continue
        if distribution.version != supplied[name]:
            raise ValueError(f"Plugin dependency conflicts with host: {name}")
        for member in distribution.files or ():
            path = Path(distribution.locate_file(member)).resolve()
            if path.is_relative_to(site.resolve()) and path.is_file():
                path.unlink()
    for directory in sorted(site.rglob("*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    if (site / "opsmesh").exists():
        raise ValueError("Plugin runtime must not shadow the framework package")


def main() -> None:
    """Vendor pinned plugin dependencies using the host's native Python ABI."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    output = args.output.resolve()
    host = json.loads((output / "opsmesh/BUILD.json").read_text())
    validate_host_runtime(host)
    version = host["version"]
    bundle = output / "plugins"
    if bundle.exists():
        raise SystemExit("Plugin output already exists; use a clean output")
    site = bundle / "site"
    site.mkdir(parents=True)
    wheels = output / "python"
    requirements = [
        str(next(wheels.glob(f"opsmesh_{name}-{version}-*.whl")))
        for name in PLUGINS
    ]
    requirements.append(str(next(wheels.glob(f"opsmesh-{version}-*.whl"))))
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-compile",
            "--no-deps",
            "--only-binary=:all:",
            "--target",
            str(site),
            "-r",
            str(ROOT / "requirements.lock"),
            "-r",
            str(ROOT / "packaging/plugins.lock"),
            *requirements,
        ],
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "pip", "check"],
        env={**os.environ, "PYTHONPATH": str(site)},
        check=True,
    )
    remove_host_dependencies(site, host["dependencies"])
    # pip console scripts target the build interpreter, not the frozen host.
    scripts = site / "bin"
    if scripts.is_dir():
        shutil.rmtree(scripts)
    inventory = sorted(
        (
            {"name": item.metadata["Name"], "version": item.version}
            for item in importlib.metadata.distributions(path=[str(site)])
        ),
        key=lambda item: item["name"].lower(),
    )
    metadata = {
        "host_version": version,
        "python": host["python"],
        "architecture": host["architecture"],
        "glibc": host["glibc"],
        "plugin_path": "site",
        "modules": [f"opsmesh_{name}" for name in PLUGINS],
        "dependencies": inventory,
    }
    (bundle / "BUILD.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copy2(ROOT / "LICENSE", bundle / "LICENSE")
    (bundle / "README.txt").write_text(
        "Optional plugins for the matching OpsMesh host release.\n"
        "Verify SHA256SUMS, extract into an administrator-owned directory,\n"
        "and add its absolute site directory to host.json plugin_paths.\n"
        "Enable only needed modules in the plugins manifest.\n"
        "The framework installer does not install or enable these plugins.\n"
        "This is an import runtime, not a Python CLI environment. External\n"
        "MCP commands and sandbox programs must be provisioned separately.\n",
        encoding="utf-8",
    )
    name = f"opsmesh-plugins-{version}-linux-{host['architecture']}.tar.gz"
    create_archive(bundle, output / name)
    print(output / name)


if __name__ == "__main__":
    main()
