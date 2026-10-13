"""Bound compressed input, ZIP members and actual decompressed reads."""

from zipfile import ZipFile

MAX_ARCHIVE_MEMBERS = 10_002
MAX_ARCHIVE_METADATA_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_INVENTORY_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_OBJECT_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_TOTAL_OBJECT_BYTES = 1024 * 1024 * 1024
ARCHIVE_METADATA_BUDGET = MAX_ARCHIVE_METADATA_BYTES + MAX_ARCHIVE_INVENTORY_BYTES


def compressed_archive_limit(max_total_bytes: int) -> int:
    # Metadata and ZIP headers are additional to the exported object-byte budget.
    return max_total_bytes + ARCHIVE_METADATA_BUDGET


class ArchiveReadLimitError(ValueError):
    def __init__(self, *, size: int, limit: int, total: bool = False) -> None:
        self.size = size
        self.limit = limit
        self.total = total
        super().__init__("Archive decompressed byte limit exceeded")


class BoundedArchiveReader:
    def __init__(self, archive: ZipFile, *, max_total_bytes: int) -> None:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("Archive contains too many members")
        self.names = {member.filename for member in members}
        if len(self.names) != len(members):
            raise ValueError("Archive contains duplicate member names")
        self._archive = archive
        self._remaining_bytes = max_total_bytes

    def read(self, name: str, *, max_bytes: int) -> bytes:
        member = self._archive.getinfo(name)
        if member.file_size > max_bytes:
            raise ArchiveReadLimitError(size=member.file_size, limit=max_bytes)
        if member.file_size > self._remaining_bytes:
            raise ArchiveReadLimitError(
                size=member.file_size, limit=self._remaining_bytes, total=True
            )
        limit = min(max_bytes, self._remaining_bytes)
        # Bound the actual read as well: declared ZIP sizes are untrusted.
        with self._archive.open(member) as stream:
            content = stream.read(limit + 1)
        self._remaining_bytes -= len(content)
        if len(content) > limit:
            raise ArchiveReadLimitError(size=len(content), limit=limit, total=limit < max_bytes)
        return content
