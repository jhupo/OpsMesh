"""Content-addressed blob storage; active workspaces remain filesystem directories."""

from .api import BlobInfo, BlobPage, BlobStore
from .local import LocalBlobStore

__all__ = ["BlobInfo", "BlobPage", "BlobStore", "LocalBlobStore"]
