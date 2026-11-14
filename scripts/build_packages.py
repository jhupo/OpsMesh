"""Build and verify the host and independently installable plugin packages."""

from __future__ import annotations

import argparse
import email
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAMES = (
    "channels",
    "cron",
    "database",
    "execution",
    "filesystem",
    "knowledge",
)


def projects(root: Path) -> tuple[Path, ...]:
    """Check that one release tag addresses all distribution versions."""
    directories = (root, *(root / "packages" / name for name in PLUGIN_NAMES))
    versions = set()
    for directory in directories:
        metadata = tomllib.loads((directory / "pyproject.toml").read_text())
        versions.add(metadata["project"]["version"])
    if len(versions) != 1:
        raise ValueError("Host and plugin distribution versions must match")
    version = versions.pop()
    for directory in directories[1:]:
        required = (directory / "requirements-core.txt").read_text().strip()
        if required != f"opsmesh=={version}":
            raise ValueError(f"Host dependency version differs in {directory}")
    return directories


def verify_wheel(path: Path) -> None:
    """Reject mixed host/plugin wheels and missing database templates."""
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        metadata_path = next(
            name for name in members if name.endswith(".dist-info/METADATA")
        )
        metadata = email.message_from_bytes(archive.read(metadata_path))
        name = metadata["Name"]
        module = "opsmesh"
        code_roots = {
            member.split("/")[0]
            for member in members
            if member.endswith((".py", ".pyi", "py.typed"))
        }
        if code_roots != {module}:
            raise ValueError(
                f"Unexpected code packages in {name}: {code_roots}"
            )
        if name == "opsmesh":
            if any(
                member.startswith(f"opsmesh/plugins/{plugin}/")
                for member in members
                for plugin in PLUGIN_NAMES
            ):
                raise ValueError("The framework wheel contains plugins")
            forbidden = (
                "openai",
                "sqlalchemy",
                "alembic",
                "asyncpg",
                "langfuse",
                "apscheduler",
                "pgvector",
                "sentence-transformers",
                "torch",
                "uvicorn",
                "httpx",
                "websockets",
                "multidict",
                "yarl",
                "opsmesh-",
            )
            if any(
                requirement.lower().startswith(forbidden)
                for requirement in metadata.get_all("Requires-Dist", [])
            ):
                raise ValueError("The framework depends on a plugin runtime")
        if name != "opsmesh":
            prefix = f"opsmesh/plugins/{name.removeprefix('opsmesh-')}/"
            code = [
                member
                for member in members
                if member.endswith((".py", ".pyi", "py.typed"))
            ]
            if any(not member.startswith(prefix) for member in code):
                raise ValueError("Plugin wheel contains framework code")
        if name == "opsmesh-database":
            if (
                "opsmesh/plugins/database/_alembic/script.py.mako"
                not in members
            ):
                raise ValueError("The database migration template is missing")


def main() -> None:
    """Produce host and plugin wheel/source pairs without installing them."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist/packages")
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for directory in projects(ROOT):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--no-isolation",
                "--outdir",
                str(output),
                str(directory),
            ],
            check=True,
        )
    wheels = list(output.glob("*.whl"))
    if len(wheels) != len(PLUGIN_NAMES) + 1:
        raise ValueError("Use a clean output directory for the release wheels")
    for wheel in wheels:
        verify_wheel(wheel)


if __name__ == "__main__":
    main()
