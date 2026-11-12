"""Errors callers may handle when using filesystem operations."""

from __future__ import annotations


class FilesystemError(Exception):
    """Base error for filesystem plugin contract violations."""


class FilesystemClosed(FilesystemError):
    """The filesystem plugin has not started or has stopped."""


class PathNotFound(FilesystemError):
    """The requested path does not exist or cannot be accessed."""


class PathRejected(FilesystemError):
    """A path is invalid, outside its roots, or cannot be used safely."""


class ArchiveRejected(FilesystemError):
    """An archive violates the configured structure or size limits."""
