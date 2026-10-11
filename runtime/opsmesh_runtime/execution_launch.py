"""Validate the supervisor's identity binding before loading an approved SDK host."""

import os
import runpy
import sys
from pathlib import Path
from uuid import UUID

from .egress_policy import execution_port


def main() -> None:
    allocation, uid, entry, *arguments = sys.argv[1:]
    uid_value = int(uid)
    execution_port(uid_value)
    if os.getuid() != uid_value or os.geteuid() != uid_value:
        raise PermissionError("Execution UID does not match its allocation")
    marker = Path(f"/run/opsmesh-identities/{uid_value}/allocation")
    if UUID(marker.read_text()) != UUID(allocation):
        raise PermissionError("Execution allocation has been revoked")
    # The process and every child inherit this unprivileged identity. Docker's
    # no-new-privileges prevents setuid binaries from regaining supervisor rights.
    os.umask(0o077)
    if entry == "--command":
        if not arguments:
            raise ValueError("Execution command is empty")
        os.execvp(arguments[0], arguments)
    if arguments or entry not in {"opsmesh.bootstrap.agent_host", "opsmesh_runtime.mcp_host"}:
        raise ValueError("Unsupported SDK execution entry point")
    sys.argv = [entry]
    runpy.run_module(entry, run_name="__main__")


if __name__ == "__main__":
    main()
