import hashlib
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ..config import Fault, identifier, require


def sync_dir(path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def directory(path):
    if not path.exists():
        directory(path.parent)
        path.mkdir(exist_ok=True)
        sync_dir(path.parent)
    require(path.is_dir() and not path.is_symlink(), "Unsafe service directory")
    return path


def move(src, dst):
    require(
        not dst.exists() and not dst.is_symlink(),
        "Unexpected destination",
        "maintenance_required",
    )
    src.rename(dst)
    sync_dir(src.parent)
    sync_dir(dst.parent)


def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
    if path.parent.exists():
        sync_dir(path.parent)


def atomic_write(path, data):
    directory(path.parent)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        tmp = Path(out.name)
        try:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        except BaseException:
            out.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
        sync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


@contextmanager
def exclusive(root):
    import fcntl

    directory(root)
    with (root / "maintenance.lock").open("ab") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Fault(
                "busy", "Another service or maintenance process owns the state"
            ) from exc
        yield


class Filesystem:
    def __init__(self, root):
        self.root = root

    def plugin(self, name):
        return self.root / "plugins" / identifier(name)

    def work(self, op):
        return self.root / "staging" / op

    def path(self, name, rel):
        parts = rel.split("/")
        require(
            all(
                part not in ("", ".", "..")
                and "\\" not in part
                and ":" not in part
                for part in parts
            ),
            "Unsafe inventory path",
        )
        folder = self.root
        for part in ("plugins", identifier(name), *parts[:-1]):
            folder = folder / part
            require(
                not folder.is_symlink()
                and (not folder.exists() or folder.is_dir()),
                f"Conflicting directory: {folder}",
                "conflict",
            )
        return folder / parts[-1]

    @staticmethod
    def digest(path):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return "not a regular file"
        if not path.exists():
            return None
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    def verify(self, name, files):
        for rel, expected in files.items():
            require(
                self.digest(self.path(name, rel)) == expected,
                f"Changed package file: {name}/{rel}",
                "conflict",
            )

    def preflight(self, name, old, new):
        self.verify(name, old)
        for rel in new.keys() - old.keys():
            require(
                self.digest(self.path(name, rel)) is None,
                f"Would overwrite local file: {name}/{rel}",
                "conflict",
            )

    def removable(self, name, files):
        unchanged, preserved = {}, []
        for rel, digest in files.items():
            try:
                current = self.digest(self.path(name, rel))
            except Fault as e:
                if e.code != "conflict":
                    raise
                current = "unsafe parent"
            if current == digest:
                unchanged[rel] = digest
            elif current is not None:
                preserved.append(rel)
        return unchanged, sorted(preserved)

    def publish(self, op, plan):
        # Each rename is durable. Staged and saved files identify the next step after a crash.
        tmp = self.work(op)
        name, old, new = (
            plan["id"],
            plan["previous_files"],
            plan["record"]["files"],
        )
        for rel in sorted(old.keys() | new.keys()):
            live = self.path(name, rel)
            saved, staged = tmp / "old" / rel, tmp / "new" / rel
            if rel in old and not saved.exists():
                require(
                    self.digest(live) == old[rel],
                    f"File changed before publish: {rel}",
                    "conflict",
                )
                directory(saved.parent)
                move(live, saved)
            if rel in new and staged.exists():
                directory(live.parent)
                move(staged, live)
            require(
                self.digest(live) == new.get(rel),
                f"Incomplete file switch: {rel}",
                "maintenance_required",
            )

        self.prune_metadata(name, old.keys() - new.keys())

    def prune_metadata(self, name, removed):
        folders = {
            parent
            for rel in removed
            if rel.startswith(".opsmesh-metadata/")
            for parent in Path(rel).parents
            if len(parent.parts) >= 2
        }
        for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
            path = self.path(name, folder.as_posix())
            if (
                path.is_dir()
                and not path.is_symlink()
                and not any(path.iterdir())
            ):
                path.rmdir()
                sync_dir(path.parent)

    def revert(self, op, plan):
        tmp = self.work(op)
        name, old, new = (
            plan["id"],
            plan["previous_files"],
            plan["record"]["files"],
        )
        for rel in sorted(old.keys() | new.keys()):
            live = self.path(name, rel)
            saved, rejected = tmp / "old" / rel, tmp / "rejected" / rel
            if rel in new and not rejected.exists():
                require(
                    self.digest(live) == new[rel],
                    f"Candidate file changed: {rel}",
                    "maintenance_required",
                )
                directory(rejected.parent)
                move(live, rejected)
            if saved.exists():
                directory(live.parent)
                move(saved, live)
            require(
                self.digest(live) == old.get(rel),
                f"Cannot restore {rel}",
                "maintenance_required",
            )

        self.prune_metadata(name, new.keys() - old.keys())

    def cleanup(self, op, plan):
        if plan["method"] == "plugin.delete":
            home = self.plugin(plan["id"])
            trash = self.root / "trash" / op
            directory(trash.parent)
            if home.exists():
                # Refuse a replaced ancestor rather than moving a directory outside plugins.
                self.path(plan["id"], "__init__.py")
                move(home, trash)
            remove(trash)
        remove(self.work(op))
