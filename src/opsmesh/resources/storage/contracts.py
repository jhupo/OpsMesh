"""Backend-neutral file metadata; storage keys are relative to an injected namespace."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class FileEntry:
    path: str
    name: str
    kind: Literal["file", "directory", "symlink", "other"]
    size: int
    modified_at: datetime
