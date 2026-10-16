"""Resource content storage: immutable blobs under bounded filesystem primitives.

Content is addressed by a fresh identifier per upload and verified by its
digest on every read, so a stored blob can never be swapped for different
bytes. Every path operation goes through ``shared.files``: nothing here
decides a business root, grants access or knows about files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from opsmesh.shared.files import atomic_replace, read_bytes_bounded, resolve_within_root

CONTENT_NAMESPACE = "resource-contents"
MAX_CONTENT_BYTES = 64 * 1024 * 1024


class ContentStorageError(Exception):
    """The stored content no longer matches what the database records."""


def new_content_id() -> UUID:
    """Fresh content identifier; one per stored blob."""
    return uuid4()


def content_digest(payload: bytes) -> str:
    """SHA-256 over the exact content bytes."""
    return hashlib.sha256(payload).hexdigest()


def content_storage_key(content_id: UUID) -> str:
    return f"{CONTENT_NAMESPACE}/{content_id}"


def write_content(storage_root: Path, *, content_id: UUID, payload: bytes) -> str:
    """Write one content blob atomically and return its digest."""
    if len(payload) > MAX_CONTENT_BYTES:
        raise ContentStorageError("The content exceeds the permitted size")
    target = resolve_within_root(storage_root, content_storage_key(content_id))
    atomic_replace(target, payload)
    return content_digest(payload)


def read_content(storage_root: Path, *, content_id: UUID, expected_digest: str) -> bytes:
    """Read one content blob back, refusing content that changed its digest."""
    path = resolve_within_root(storage_root, content_storage_key(content_id))
    payload = read_bytes_bounded(path, max_bytes=MAX_CONTENT_BYTES)
    if content_digest(payload) != expected_digest:
        raise ContentStorageError("The stored content no longer matches its digest")
    return payload


def delete_content(storage_root: Path, *, content_id: UUID) -> None:
    """Remove one content blob; a missing blob is already gone."""
    path = resolve_within_root(storage_root, content_storage_key(content_id))
    path.unlink(missing_ok=True)
