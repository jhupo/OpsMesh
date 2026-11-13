"""Inventory or migrate explicitly named plugin directories while offline."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path

from opsmesh.app import _data_lock
from opsmesh.plugins.filesystem._files import (
    _publish_new_directory,
    write_bytes,
)

KINDS = ("data", "cache", "config")
JOURNAL = ".plugin-path-migration.json"


@dataclass(frozen=True)
class Move:
    """One explicit legacy directory and its canonical replacement."""

    source: Path
    destination: Path

    @property
    def key(self) -> str:
        """Identify an operation independently of processing order."""
        return str(self.source)


def read_plugins(manifest: Path) -> tuple[str, ...]:
    """Read a complete inventory without discovering plugins from paths."""
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"plugins"}:
        raise ValueError("The manifest must contain only a plugins array")
    modules = value["plugins"]
    if not isinstance(modules, list) or not modules:
        raise ValueError("The plugin inventory must be a nonempty array")
    for module in modules:
        if not isinstance(module, str) or not all(
            part.isidentifier() for part in module.split(".")
        ):
            raise ValueError(
                "Plugin names must be absolute Python module names"
            )
        if len(module.encode("utf-8")) > 255:
            raise ValueError("A plugin name exceeds the directory name limit")
    if len(set(modules)) != len(modules):
        raise ValueError("Plugin names must be unique")
    return tuple(sorted(modules))


def inventory(root: Path, modules: tuple[str, ...]) -> tuple[Move, ...]:
    """Produce deepest-first moves and reject links in managed paths."""
    root = root.resolve(strict=True)
    moves = []
    for kind in KINDS:
        base = root / kind
        for module in sorted(
            modules, key=lambda name: (-name.count("."), name)
        ):
            source = base.joinpath(*module.split("."))
            destination = base / module
            for path in (source, destination):
                _check_path(root, path)
            moves.append(Move(source, destination))
    return tuple(moves)


def unknown_directories(root: Path, modules: tuple[str, ...]) -> list[str]:
    """Report unowned top-level entries without moving or guessing owners."""
    known = set(modules) | {module.split(".")[0] for module in modules}
    return [
        str(path)
        for kind in KINDS
        if (root / kind).is_dir()
        for path in sorted((root / kind).iterdir())
        if path.name not in known
    ]


def migrate(root: Path, modules: tuple[str, ...]) -> None:
    """Preflight every target and retain a resumable atomic-move journal."""
    root = root.resolve(strict=True)
    moves = inventory(root, modules)
    journal_path = root / JOURNAL
    _check_path(root, journal_path)
    journal = {"version": 1, "plugins": list(modules), "moves": {}}
    if journal_path.exists():
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if journal.get("version") != 1 or journal.get("plugins") != list(
            modules
        ):
            raise ValueError(
                "The migration journal belongs to another inventory"
            )
    records = journal["moves"]
    for move in moves:
        if move.source == move.destination:
            continue
        record = records.get(move.key)
        if move.destination.exists():
            identity = _identity(move.destination)
            if (
                move.source.exists()
                or record is None
                or identity != record["identity"]
            ):
                raise ValueError(f"Conflicting destination: {move.destination}")
        elif record is not None and not move.source.exists():
            raise ValueError(f"Both migration paths are missing: {move.source}")
        if move.source.exists() and not move.source.is_dir():
            raise ValueError(f"The source is not a directory: {move.source}")
    for move in moves:
        if move.source == move.destination:
            continue
        if not move.source.exists():
            if move.key in records:
                records[move.key]["completed"] = True
                _save(journal_path, journal)
            continue
        record = {"identity": _identity(move.source), "completed": False}
        records[move.key] = record
        _save(journal_path, journal)
        _publish_new_directory(move.source, move.destination)
        _sync_directory(move.source.parent)
        _sync_directory(move.destination.parent)
        record["completed"] = True
        _save(journal_path, journal)


def _identity(path: Path) -> list[int]:
    info = path.stat(follow_symlinks=False)
    return [info.st_dev, info.st_ino]


def _check_path(root: Path, path: Path) -> None:
    if not path.is_relative_to(root):
        raise ValueError("A migration path is outside its storage root")
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise ValueError(f"Symbolic links cannot be migrated: {component}")


def _save(path: Path, journal: dict) -> None:
    write_bytes(
        path, json.dumps(journal, indent=2).encode("utf-8"), replace=True
    )
    _sync_directory(path.parent)


def _sync_directory(path: Path) -> None:
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def main() -> None:
    """Print inventory by default and require explicit offline application."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    modules = read_plugins(arguments.manifest)
    root = arguments.root.resolve(strict=True)
    with _data_lock(arguments.state_dir.resolve()):
        moves = inventory(root, modules)
        print(
            json.dumps(
                {
                    "moves": [
                        {
                            "source": str(move.source),
                            "destination": str(move.destination),
                        }
                        for move in moves
                        if move.source != move.destination
                    ],
                    "unregistered_entries": unknown_directories(root, modules),
                },
                indent=2,
            )
        )
        if arguments.apply:
            migrate(root, modules)


if __name__ == "__main__":
    main()
