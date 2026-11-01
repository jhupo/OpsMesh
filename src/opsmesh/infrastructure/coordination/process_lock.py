"""Nonblocking local process ownership tied to an OS-held file descriptor."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import BinaryIO


class LocalProcessLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        if self._file is not None:
            raise RuntimeError("Process lock is already acquired")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        stream = self.path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise RuntimeError("Another process owns this resource") from error
        self._file = stream

    def release(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
