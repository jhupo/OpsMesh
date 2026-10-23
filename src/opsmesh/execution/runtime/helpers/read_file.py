"""Read a bounded regular file using descriptor-relative, no-follow traversal."""

import os
import stat
import sys


def main() -> None:
    directory = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = sys.argv[2].split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise ValueError("Invalid path")
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(
            parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("Only regular files may be transferred")
            remaining = int(sys.argv[3]) + 1
            while remaining:
                data = os.read(descriptor, min(remaining, 65536))
                if not data:
                    break
                sys.stdout.buffer.write(data)
                remaining -= len(data)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError:
        sys.exit(66)
    except PermissionError:
        sys.exit(77)
