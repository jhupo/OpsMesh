import io
import shutil
from pathlib import Path
from typing import Any

from agents.sandbox.files import EntryKind
from agents.sandbox.session.sandbox_session import SandboxSession
from sqlalchemy.orm import Session

from opsmesh.sessions.sandboxes import SandboxStorage, sandbox_path
from opsmesh.shared.errors import NotFoundError

from .models import Workspace
from .workspaces import directory, safe_path, write


class LocalFiles:
    def __init__(self, db: Session, root: Path, workspace: Workspace, limit: int):
        self.db, self.root, self.workspace, self.limit = db, root, workspace, limit
        self.folder = directory(root, workspace)
        self.folder.mkdir(parents=True, exist_ok=True)

    async def list(self, path: str) -> list[dict[str, Any]]:
        target = safe_path(self.folder, path) if path else self.folder
        if not target.is_dir():
            raise NotFoundError("Directory not found")
        return [
            {
                "path": item.relative_to(self.folder).as_posix(),
                "directory": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else 0,
            }
            for item in sorted(target.iterdir())[:2000]
            if not item.is_symlink() and not item.is_junction()
        ]

    async def read(self, path: str) -> bytes:
        target = safe_path(self.folder, path)
        if not target.is_file():
            raise NotFoundError("File not found")
        with target.open("rb") as source:
            value = source.read(self.limit + 1)
        if len(value) > self.limit:
            raise ValueError("File exceeds the download limit")
        return value

    async def write(self, path: str, content: bytes) -> None:
        if len(content) > self.limit:
            raise ValueError("File exceeds the upload limit")
        write(self.db, self.root, self.workspace, path, content)

    async def remove(self, path: str) -> None:
        target = safe_path(self.folder, path)
        if target.is_dir():
            if target.resolve() == self.folder.resolve() or not target.resolve().is_relative_to(
                self.folder.resolve()
            ):
                raise ValueError("Cannot remove the managed storage root")
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
        else:
            raise NotFoundError("File or directory not found")

    async def mkdir(self, path: str) -> None:
        safe_path(self.folder, path).mkdir(parents=True, exist_ok=True)


class SandboxFiles:
    def __init__(self, live: SandboxSession, storage: SandboxStorage, session_id: str, limit: int):
        self.live, self.storage, self.session_id, self.limit = live, storage, session_id, limit

    def path(self, name: str, *, allow_root: bool = False) -> Path:
        return sandbox_path(self.live.state.manifest.root, name, allow_root=allow_root)

    async def list(self, path: str) -> list[dict[str, Any]]:
        return [
            {
                "path": str(Path(path) / Path(entry.path).name).replace("\\", "/"),
                "directory": entry.kind == EntryKind.DIRECTORY,
                "size": entry.size,
            }
            for entry in await self.live.ls(self.path(path, allow_root=True))
            if Path(entry.path).name != ".opsmesh"
        ]

    async def read(self, path: str) -> bytes:
        stream = await self.live.read(self.path(path))
        try:
            value = stream.read(self.limit + 1)
        finally:
            stream.close()
        if len(value) > self.limit:
            raise ValueError("File exceeds the download limit")
        return value

    async def write(self, path: str, content: bytes) -> None:
        target = self.path(path)
        if len(content) > self.limit:
            raise ValueError("File exceeds the upload limit")
        self.storage.check_write(self.session_id, len(content))
        await self.live.mkdir(target.parent, parents=True)
        await self.live.write(target, io.BytesIO(content))

    async def remove(self, path: str) -> None:
        await self.live.rm(self.path(path), recursive=True)

    async def mkdir(self, path: str) -> None:
        await self.live.mkdir(self.path(path), parents=True)
