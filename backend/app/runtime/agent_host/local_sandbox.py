"""Filesystem/process operations inside the approved Runtime, never on the Worker host."""

import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from backend.app.runtime.contracts import SandboxCommandResult


@dataclass(frozen=True)
class LocalSandboxExecutor:
    root: str
    timeout_seconds: int
    max_file_bytes: int

    def _path(self, path: PurePosixPath) -> Path:
        target = Path(str(path)).resolve()
        if not target.is_relative_to(Path(self.root).resolve()):
            raise ValueError("SDK file access is outside its Runtime workspace")
        return target

    def execute(
        self, command: list[str], *, timeout_seconds: int, working_dir: str
    ) -> SandboxCommandResult:
        directory = self._path(PurePosixPath(working_dir))
        timeout = max(1, min(math.ceil(timeout_seconds), self.timeout_seconds))
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            result = subprocess.run(
                command, cwd=directory, stdout=stdout, stderr=stderr, timeout=timeout, check=False
            )
            stdout.seek(0)
            stderr.seek(0)
            return SandboxCommandResult(
                result.returncode,
                stdout.read(self.max_file_bytes),
                stderr.read(self.max_file_bytes),
            )

    def read_file(self, path: PurePosixPath) -> bytes | None:
        target = self._path(path)
        if not target.is_file():
            return None
        with target.open("rb") as source:
            content = source.read(self.max_file_bytes + 1)
        if len(content) > self.max_file_bytes:
            raise ValueError("Sandbox file exceeds the Runtime transfer limit")
        return content

    def write_file(self, path: PurePosixPath, data: BinaryIO) -> None:
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = data.read(self.max_file_bytes + 1)
        if not isinstance(content, bytes):
            raise TypeError("Sandbox file writes require a binary stream")
        if len(content) > self.max_file_bytes:
            raise ValueError("Sandbox file exceeds the Runtime transfer limit")
        target.write_bytes(content)

    def running(self) -> bool:
        return True
