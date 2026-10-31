"""Atomic, non-overwriting local blobs compatible with the existing objects directory."""

import asyncio
import hashlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError

from .api import BlobInfo, BlobPage, validate_digest, validate_page_limit


class LocalBlobStore:
    def __init__(self, root: Path, *, max_blob_bytes: int = 64 << 20) -> None:
        if max_blob_bytes <= 0:
            raise OpsMeshError("CONFIG_INVALID", "Blob size limit must be positive")
        if root.is_symlink():
            raise OpsMeshError("PATH_INVALID", "Blob root cannot be a symlink")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._limit = max_blob_bytes

    @property
    def identity(self) -> str:
        return f"file:{os.path.normcase(str(self.root))}"

    def _path(self, digest: str) -> Path:
        path = self.root / validate_digest(digest)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise OpsMeshError("CONTENT_CORRUPT", "Blob path must be a regular file")
        return path

    async def put(self, data: bytes) -> str:
        return await asyncio.to_thread(self._put, data)

    def _put(self, data: bytes) -> str:
        if len(data) > self._limit:
            raise OpsMeshError("RESOURCE_EXHAUSTED", "Blob exceeds the configured size limit")
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(digest)
        temporary = self.root / f".{digest}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # A link publishes only complete bytes and cannot overwrite another writer.
                os.link(temporary, path)
                if os.name != "nt":
                    directory = os.open(self.root, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
            except FileExistsError:
                self._get(digest)
        finally:
            temporary.unlink(missing_ok=True)
        return digest

    async def get(self, digest: str) -> bytes:
        return await asyncio.to_thread(self._get, digest)

    def _get(self, digest: str) -> bytes:
        path = self._path(digest)
        try:
            with path.open("rb") as stream:
                data = stream.read(self._limit + 1)
        except FileNotFoundError as error:
            raise OpsMeshError("NOT_FOUND", "Blob not found") from error
        if len(data) > self._limit or hashlib.sha256(data).hexdigest() != digest:
            raise OpsMeshError("CONTENT_CORRUPT", "Blob size or content digest mismatch")
        return data

    async def stat(self, digest: str) -> BlobInfo:
        return await asyncio.to_thread(self._stat, digest)

    async def is_empty(self) -> bool:
        return await asyncio.to_thread(lambda: not any(self.root.iterdir()))

    def _stat(self, digest: str) -> BlobInfo:
        try:
            value = self._path(digest).stat()
        except FileNotFoundError as error:
            raise OpsMeshError("NOT_FOUND", "Blob not found") from error
        return BlobInfo(
            digest=digest,
            size=value.st_size,
            modified_at=datetime.fromtimestamp(value.st_mtime, UTC),
            version=f"{value.st_ino}:{value.st_size}:{value.st_mtime_ns}:{value.st_ctime_ns}",
        )

    async def list(self, *, continuation: str | None = None, limit: int = 1000) -> BlobPage:
        validate_page_limit(limit)
        if continuation is not None:
            validate_digest(continuation)
        return await asyncio.to_thread(self._list, continuation, limit)

    def _list(self, continuation: str | None, limit: int) -> BlobPage:
        paths = sorted(
            path
            for path in self.root.iterdir()
            if re.fullmatch(r"[a-f0-9]{64}", path.name)
            and not path.is_symlink()
            and path.is_file()
            and (continuation is None or path.name > continuation)
        )
        items: list[BlobInfo] = []
        for path in paths[:limit]:
            try:
                items.append(self._stat(path.name))
            except OpsMeshError as error:
                if error.code != "NOT_FOUND":
                    raise
        return BlobPage(
            items=tuple(items),
            continuation=paths[limit - 1].name if len(paths) > limit else None,
        )

    async def delete(self, digest: str, *, expected_version: str) -> bool:
        return await asyncio.to_thread(self._delete, digest, expected_version)

    def _delete(self, digest: str, expected_version: str) -> bool:
        try:
            info = self._stat(digest)
        except OpsMeshError as error:
            if error.code == "NOT_FOUND":
                return False
            raise
        if info.version != expected_version:
            raise OpsMeshError("CONFLICT", "Blob changed since inspection")
        # WorkspaceService holds the shared DB fence across this operation. Unmanaged
        # filesystem writes are outside this namespace's concurrency contract.
        try:
            self._path(digest).unlink()
            return True
        except FileNotFoundError:
            return False

    async def close(self) -> None:
        """Local blobs have no open shared client or background resource."""
