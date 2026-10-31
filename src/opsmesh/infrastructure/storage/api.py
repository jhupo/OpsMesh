"""Immutable SHA-256 blobs and paged inventory, separate from POSIX workspaces."""

import re
from datetime import datetime
from typing import Protocol

from pydantic import Field

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract


class BlobInfo(OpsMeshContract):
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)
    modified_at: datetime
    version: str


class BlobPage(OpsMeshContract):
    items: tuple[BlobInfo, ...] = ()
    continuation: str | None = None


class BlobStore(Protocol):
    @property
    def identity(self) -> str:
        """Stable, secret-free lock scope for one physical namespace."""
        ...

    async def put(self, data: bytes) -> str:
        """Create once or verify existing content; never replace a corrupt object."""
        ...

    async def get(self, digest: str) -> bytes:
        """Read with a bounded size and verify the actual bytes against the digest."""
        ...

    async def stat(self, digest: str) -> BlobInfo: ...

    async def list(self, *, continuation: str | None = None, limit: int = 1000) -> BlobPage: ...

    async def is_empty(self) -> bool:
        """Check the whole owned namespace, including unexpected non-blob objects."""
        ...

    async def delete(self, digest: str, *, expected_version: str) -> bool:
        """Delete a known revision, or return False if absent; caller owns reference safety."""
        ...

    async def close(self) -> None: ...


def validate_digest(digest: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise OpsMeshError("CONFIG_INVALID", "Invalid content digest")
    return digest


def validate_page_limit(limit: int) -> None:
    if not 1 <= limit <= 1000:
        raise OpsMeshError("CONFIG_INVALID", "Blob page size must be between 1 and 1000")
