"""Apply workspace ACLs without following symlinks or granting hard-linked files."""

import errno
import os
import shutil
import stat
import subprocess
import sys


def apply(
    descriptor: int, permissions: str, uid: int, group: int, *, default: bool = False
) -> None:
    executable = shutil.which("setfacl")
    if executable is None:
        raise RuntimeError("The compute image requires the acl package")
    rules = (
        f"u::{permissions},u:{uid}:{permissions},g::---,"
        f"g:{group}:{permissions},m::{permissions},o::---"
    )
    command = [executable, "--set", rules, f"/proc/self/fd/{descriptor}"]
    if default:
        command.insert(1, "--default")
    subprocess.run(command, pass_fds=(descriptor,), check=True, capture_output=True)


def main() -> None:
    root, uid_arg, group_arg = sys.argv[1:]
    uid, group = int(uid_arg), int(group_arg)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for _, _, names, descriptor in os.fwalk(".", dir_fd=directory, follow_symlinks=False):
            apply(descriptor, "rwx", uid, group)
            apply(descriptor, "rwx", uid, group, default=True)
            for name in names:
                try:
                    file = os.open(
                        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
                    )
                except OSError as error:
                    if error.errno in {errno.ENOENT, errno.ELOOP}:
                        continue
                    raise
                try:
                    metadata = os.fstat(file)
                    if stat.S_ISREG(metadata.st_mode):
                        if metadata.st_nlink != 1:
                            raise RuntimeError("Hard-linked workspace files cannot receive ACLs")
                        apply(file, "rwx" if metadata.st_mode & 0o111 else "rw-", uid, group)
                finally:
                    os.close(file)
    finally:
        os.close(directory)


if __name__ == "__main__":
    main()
