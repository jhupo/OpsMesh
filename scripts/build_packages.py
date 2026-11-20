"""Build and verify the host and independently installable plugin packages."""

from __future__ import annotations

import argparse
import configparser
import email
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_NAMES = (
    "dingtalk",
    "database",
    "filesystem",
    "knowledge",
    "qq",
)


def projects(root: Path) -> tuple[Path, ...]:
    """Check that one release tag addresses all distribution versions."""
    cli = root / "src/opsmesh-cli"
    directories = (
        root, cli, *(root / "packages" / name for name in PLUGIN_NAMES)
    )
    versions = set()
    for directory in directories:
        metadata = tomllib.loads((directory / "pyproject.toml").read_text())
        versions.add(metadata["project"]["version"])
    if len(versions) != 1:
        raise ValueError("Framework, CLI and plugin versions must match")
    version = versions.pop()
    cli_metadata = tomllib.loads((cli / "pyproject.toml").read_text())
    if f"opsmesh=={version}" not in cli_metadata["project"]["dependencies"]:
        raise ValueError("Service must depend on the matching framework version")
    for directory in directories[2:]:
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
        code_roots = {
            member.split("/")[0]
            for member in members
            if member.endswith((".py", ".pyi", "py.typed"))
        }
        standalone = {
            "opsmesh-qq": "qq",
            "opsmesh-dingtalk": "dingtalk",
            "opsmesh-cli": "opsmesh_cli",
            "opsmesh-database": "database",
            "opsmesh-filesystem": "filesystem",
            "opsmesh-knowledge": "knowledge",
        }
        if code_roots != {standalone.get(name, "opsmesh")}:
            raise ValueError(
                f"Unexpected code packages in {name}: {code_roots}"
            )
        if name == "opsmesh":
            if any(
                member.startswith(("opsmesh/bootstrap/", "opsmesh/service/"))
                or member in {"opsmesh/main.py", "opsmesh/plugins/storage.py"}
                or member.endswith(".dist-info/entry_points.txt")
                for member in members
            ):
                raise ValueError("Framework wheel contains service files")
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
                "opsmesh-",
            )
            if any(
                dep.name.lower().startswith(forbidden) and (not dep.marker or dep.marker.evaluate({"extra": ""}))
                for dep in map(Requirement, metadata.get_all("Requires-Dist", []))
            ):
                raise ValueError("The framework depends on a plugin runtime")
        if name == "opsmesh-cli":
            entry_points = configparser.ConfigParser()
            for member in members:
                if member.endswith(".dist-info/entry_points.txt"):
                    entry_points.read_string(archive.read(member).decode())
            if (
                entry_points.sections() != ["console_scripts"]
                or dict(entry_points["console_scripts"])
                != {"opsmesh": "opsmesh_cli.main:main"}
            ):
                raise ValueError("Unexpected CLI wheel entry points")
            # Check every Python module declared by the flat CLI project.
            cli = ROOT / "src/opsmesh-cli"
            packages = tomllib.loads((cli / "pyproject.toml").read_text())["tool"]["setuptools"]["packages"]
            expected = {
                f"{package.replace('.', '/')}/{module.name}"
                for package in packages
                for module in (cli / 'src').joinpath(*package.split(".")[1:]).glob("*.py")
            }
            cli_code = {
                member for member in members if member.endswith((".py", ".pyi"))
            }
            if cli_code != expected or "opsmesh_cli/main.py" not in cli_code:
                raise ValueError("CLI wheel does not match its declared runtime packages")
        elif name != "opsmesh":
            prefix = standalone[name] + "/" if name in standalone else f"opsmesh/plugins/{name.removeprefix('opsmesh-')}/"
            code = [
                member
                for member in members
                if member.endswith((".py", ".pyi", "py.typed"))
            ]
            if any(not member.startswith(prefix) for member in code):
                raise ValueError("Plugin wheel contains framework code")
        if name == "opsmesh-database":
            if (
                "database/_alembic/script.py.mako"
                not in members
            ):
                raise ValueError("The database migration template is missing")
        if name == "opsmesh-knowledge" and not any(
            member.startswith("knowledge/migrations/") and member.endswith(".py")
            for member in members
        ):
            raise ValueError("The knowledge wheel is missing migrations")


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
    if len(wheels) != len(PLUGIN_NAMES) + 2:
        raise ValueError("Use a clean output directory for the release wheels")
    for wheel in wheels:
        verify_wheel(wheel)


if __name__ == "__main__":
    main()
