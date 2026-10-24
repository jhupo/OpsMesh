"""Local plugin packaging. No host service or business API client is imported."""

import argparse
import zipfile
from pathlib import Path

import yaml

from .manifest import PluginManifest


def package_plugin(directory: Path, output: Path) -> None:
    directory, output = directory.resolve(), output.resolve()
    manifest = directory / "plugin.yaml"
    if not manifest.is_file() or not (directory / "main.py").is_file():
        raise ValueError("A plugin requires plugin.yaml and main.py")
    PluginManifest.model_validate(yaml.safe_load(manifest.read_text(encoding="utf-8")))
    files = []
    for path in directory.rglob("*"):
        if any(
            part in {"__pycache__", ".git", ".venv"} for part in path.relative_to(directory).parts
        ):
            continue
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Plugin packages cannot contain links")
        if path.is_file() and path != output:
            files.append(path)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(directory).as_posix())


def main() -> None:
    parser = argparse.ArgumentParser(description="OpsMesh plugin development tools")
    commands = parser.add_subparsers(dest="command", required=True)
    package = commands.add_parser("package")
    package.add_argument("directory", type=Path)
    package.add_argument("output", type=Path)
    arguments = parser.parse_args()
    try:
        package_plugin(arguments.directory, arguments.output)
    except (ValueError, OSError, yaml.YAMLError) as exc:
        parser.exit(2, f"{exc}\n")
