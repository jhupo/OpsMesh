"""Squid external ACL helper. No URL, credential or argument logging."""

import json
import sys
from pathlib import Path
from urllib.parse import unquote

from .egress_policy import (
    EXECUTION_SLOTS,
    FIRST_EXECUTION_UID,
    FIRST_PROXY_PORT,
    ExecutionNetworkPolicy,
)

POLICY_ROOT = Path("/run/opsmesh-executions")


def permits(line: str) -> bool:
    try:
        fields = line.split()
        if len(fields) != 4 or fields[3] != "-":
            return False
        incoming, host, destination = fields[:3]
        slot = int(incoming) - FIRST_PROXY_PORT
        if not 0 <= slot < EXECUTION_SLOTS:
            return False
        uid = FIRST_EXECUTION_UID + slot
        policy = ExecutionNetworkPolicy.parse(
            json.loads((POLICY_ROOT / f"{uid}.json").read_bytes())
        )
        return policy.uid == uid and policy.permits_proxy(unquote(host), int(destination))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def main() -> None:
    while line := sys.stdin.readline(4097):
        if len(line) > 4096 or not line.endswith("\n"):
            raise ValueError("Invalid ACL request")
        print("OK" if permits(line) else "ERR", flush=True)


if __name__ == "__main__":
    main()
