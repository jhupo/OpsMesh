"""Internal storage ports. Callers authorize access before invoking these methods."""

from abc import ABC, abstractmethod
from pathlib import Path

from opsmesh.resources.storage.contracts import FileEntry
from opsmesh.shared.pagination import PageParams


class FileStorage(ABC):
    max_transfer_bytes: int

    @abstractmethod
    def ensure_directory(self, storage_key: str) -> None: ...

    @abstractmethod
    def list_entries(
        self, storage_key: str, *, path: str, page: PageParams
    ) -> tuple[list[FileEntry], int]: ...

    @abstractmethod
    def read_file(self, storage_key: str, *, path: str) -> bytes: ...

    @abstractmethod
    def write_file(self, storage_key: str, *, path: str, content: bytes) -> None: ...

    @abstractmethod
    def create_subdirectory(self, storage_key: str, *, path: str) -> None: ...

    @abstractmethod
    def delete_entry(self, storage_key: str, *, path: str) -> None: ...


class LocalDirectoryStorage(FileStorage):
    """Additional capability for consumers that require a local mount or native path."""

    @abstractmethod
    def local_directory(self, storage_key: str) -> Path:
        """Materialize and return a local directory after business authorization."""
