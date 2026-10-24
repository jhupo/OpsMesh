"""Inspect and build bounded plugin ZIPs without importing plugin entry points."""

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from opsmesh_sdk.manifest import PluginManifest, parse_manifest

MAX_PACKAGE_SIZE = 50_000_000
MAX_UNPACKED_SIZE = 200_000_000


def inspect_package(path: Path) -> PluginManifest:
    if path.stat().st_size > MAX_PACKAGE_SIZE:
        raise ValueError("Plugin ZIP exceeds 50 MB")
    with zipfile.ZipFile(path) as archive:
        items = archive.infolist()
        if len(items) > 10_000 or sum(item.file_size for item in items) > MAX_UNPACKED_SIZE:
            raise ValueError("Plugin archive exceeds extraction limits")
        names: set[str] = set()
        for item in items:
            name = PurePosixPath(item.filename)
            mode = item.external_attr >> 16
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in item.filename
                or "\\" in item.orig_filename
                or ":" in item.filename
                or item.orig_filename != item.filename
                or any(ord(char) < 32 for char in item.filename)
                or not name.parts
                or str(name) in names
                or name.parts[0] in {"opsmesh", "opsmesh_sdk"}
                or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR})
                or item.flag_bits & 1
            ):
                raise ValueError("Unsafe plugin archive member")
            names.add(str(name))
        prefix = _prefix(archive)
        info = archive.getinfo(prefix + "plugin.yaml")
        if info.file_size > 64_000:
            raise ValueError("Plugin manifest exceeds 64 KB")
        manifest = parse_manifest(archive.read(info))
        if prefix + manifest.entry not in names:
            raise ValueError("The plugin entry module is absent")
        return manifest


def _prefix(archive: zipfile.ZipFile) -> str:
    files = [item.filename for item in archive.infolist() if not item.is_dir()]
    if "plugin.yaml" in files:
        return ""
    roots = {name.split("/")[0] for name in files}
    if len(roots) == 1:
        prefix = next(iter(roots)) + "/"
        if prefix + "plugin.yaml" in files:
            return prefix
    raise ValueError("Archive must contain plugin.yaml at its root or in one enclosing directory")


def extract_package(source: Path, destination: Path) -> PluginManifest:
    manifest = inspect_package(source)
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(source) as archive:
        prefix = _prefix(archive)
        for item in archive.infolist():
            if item.is_dir():
                continue
            target = destination / item.filename[len(prefix) :]
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as content, target.open("xb") as output:
                shutil.copyfileobj(content, output)
    return manifest


def build_package(source: Path, output: Path) -> PluginManifest:
    source = source.resolve(strict=True)
    output = output.resolve()
    if output.is_relative_to(source):
        raise ValueError("Write the ZIP outside its source directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    try:
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Plugin source cannot contain symbolic links")
                if path.is_file() and not any(
                    part.startswith(".") or part == "__pycache__"
                    for part in path.relative_to(source).parts
                ):
                    archive.write(path, path.relative_to(source).as_posix())
        return inspect_package(output)
    except BaseException:
        output.unlink()
        raise
