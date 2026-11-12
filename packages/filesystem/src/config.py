"""Filesystem plugin settings owned by the plugin, not the host."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    """Configure plugin roots, relative paths, and import/copy limits.

    Byte and entry limits apply to ZIP imports and directory copies. Ordinary
    file reads/writes and ZIP exports are controlled by their calling plugin.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: Path
    max_archive_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    max_extracted_bytes: int = Field(default=512 * 1024 * 1024, gt=0)
    max_file_bytes: int = Field(default=64 * 1024 * 1024, gt=0)
    max_entries: int = Field(default=10_000, gt=0)
    max_path_length: int = Field(default=512, gt=0)
    max_depth: int = Field(default=32, gt=0)
