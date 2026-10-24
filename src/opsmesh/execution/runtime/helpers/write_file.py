"""Atomically commit a staged file without following workspace symlinks."""

import os
import stat
import sys
from contextlib import suppress


def main() -> None:
    root, relative, staged, limit = sys.argv[1:]
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = None
    try:
        parts = relative.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("Invalid path")
        for part in parts[:-1]:
            with suppress(FileExistsError):
                os.mkdir(part, mode=0o770, dir_fd=directory)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        source = os.open(staged, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(source, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > int(limit):
                raise ValueError("Invalid staged file")
            data = stream.read(int(limit) + 1)
            if len(data) > int(limit):
                raise ValueError("Oversized staged file")
        temporary = ".opsmesh-write-" + os.path.basename(staged)
        # Preserve write access for the API/Worker service group; all other users are denied.
        destination = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o660, dir_fd=directory
        )
        with os.fdopen(destination, "wb") as stream:
            os.fchmod(stream.fileno(), 0o660)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, parts[-1], src_dir_fd=directory, dst_dir_fd=directory)
        temporary = None
        os.fsync(directory)
    finally:
        if temporary:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory)
        os.close(directory)
        with suppress(FileNotFoundError):
            os.unlink(staged)


if __name__ == "__main__":
    main()
