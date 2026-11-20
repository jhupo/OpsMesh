"""Cold backups. Run under the same OS identity as the daemon, never as root."""

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from .config import require
from .storage.filesystem import (
    Filesystem,
    atomic_write,
    directory,
    exclusive,
    move,
    remove,
    sync_dir,
)


def inventory(root):
    result = {}
    for p in sorted(root.rglob("*")):
        require(
            not p.is_symlink() and (p.is_file() or p.is_dir()),
            "Special file in backup set",
        )
        if p.is_file() and p != root / "snapshot.json":
            with p.open("rb") as f:
                result[p.relative_to(root).as_posix()] = hashlib.file_digest(
                    f, "sha256"
                ).hexdigest()
    return result


def check_database(root):
    with closing(
        sqlite3.connect(
            (root / "state.sqlite3").as_uri() + "?mode=ro", uri=True
        )
    ) as db:
        require(
            db.execute("PRAGMA integrity_check").fetchone() == ("ok",),
            "Database integrity check failed",
        )
        require(
            db.execute("PRAGMA user_version").fetchone() == (2,),
            "Unsupported schema; no migration path",
        )
        require(
            not db.execute(
                "SELECT 1 FROM operations WHERE phase NOT IN ('succeeded','failed')"
            ).fetchone(),
            "Unfinished operation; cannot snapshot",
            "busy",
        )
        for key, raw in db.execute("SELECT id,record FROM plugins"):
            from .config import identifier

            r = json.loads(raw)
            fs = Filesystem(root)
            for rel in r["files"]:
                fs.path(identifier(key), rel)


def copy_plugins(src, dst):
    directory(dst)
    if not src.exists():
        return
    require(not src.is_symlink(), "Unsafe plugins directory")
    for plugin in src.iterdir():
        require(
            plugin.is_dir() and not plugin.is_symlink(),
            "Unsafe plugin directory",
        )
        inventory(plugin)
        shutil.copytree(plugin, dst / plugin.name, symlinks=True)


def durable(root):
    for parent, _, files in os.walk(root, topdown=False):
        for name in files:
            with (Path(parent) / name).open("rb") as f:
                os.fsync(f.fileno())
        sync_dir(Path(parent))


def maintain(cfg, args):
    require(
        os.name == "posix" and os.getuid() != 0,
        "Run maintenance as the service user",
        "permission_denied",
    )
    root = cfg.data
    with exclusive(root):
        pending = root / "restore.pending"
        require(
            not pending.exists() or args.action == "restore",
            "Resume the pending restore first",
        )
        if args.action == "backup":
            check_database(root)
            sid = str(uuid.uuid4())
            temp = directory(cfg.backups / ("." + sid))
            try:
                with (
                    closing(sqlite3.connect(root / "state.sqlite3")) as src,
                    closing(sqlite3.connect(temp / "state.sqlite3")) as dst,
                ):
                    src.backup(dst)
                copy_plugins(root / "plugins", temp / "plugins")
                check_database(temp)
                atomic_write(
                    temp / "snapshot.json",
                    json.dumps(inventory(temp), sort_keys=True).encode(),
                )
                durable(temp)
                move(temp, cfg.backups / sid)
                return {"snapshot": sid}
            finally:
                remove(temp)
        sid = getattr(args, "snapshot", None)
        if not sid:
            check_database(root)
            return {"schema": 2, "valid": True, "migration_required": False}
        require(str(uuid.UUID(sid)) == sid, "Invalid snapshot ID")
        source = cfg.backups / sid
        require(
            source.is_dir() and not source.is_symlink(),
            "Snapshot not found",
            "not_found",
        )
        require(
            inventory(source)
            == json.loads((source / "snapshot.json").read_bytes()),
            "Snapshot checksum mismatch",
        )
        check_database(source)
        if args.action == "check":
            return {"valid": True, "snapshot": sid}
        require(args.confirm == sid, "Confirm snapshot ID", "conflict")
        if pending.exists():
            job = json.loads(pending.read_bytes())
            require(job["snapshot"] == sid, "Different restore pending")
        else:
            job = {"snapshot": sid, "id": str(uuid.uuid4())}
            tmp = directory(root / ("restore-" + job["id"]))
            copy_plugins(source / "plugins", tmp / "plugins")
            shutil.copyfile(source / "state.sqlite3", tmp / "state.sqlite3")
            require(
                inventory(tmp)
                == json.loads((source / "snapshot.json").read_bytes()),
                "Restore copy mismatch",
            )
            durable(tmp)
            atomic_write(pending, json.dumps(job).encode())
        tmp = root / ("restore-" + job["id"])
        old = directory(root / ("previous-" + job["id"]))
        for name in (
            "plugins",
            "state.sqlite3",
            "staging",
            "trash",
            "state.sqlite3-wal",
            "state.sqlite3-shm",
            "state.sqlite3-journal",
        ):
            if (
                name in ("plugins", "state.sqlite3")
                and not (tmp / name).exists()
            ):
                continue
            if (root / name).exists():
                move(root / name, old / name)
            if (tmp / name).exists():
                move(tmp / name, root / name)
        check_database(root)
        remove(tmp)
        remove(pending)
        return {"restored": sid, "previous": str(old)}
