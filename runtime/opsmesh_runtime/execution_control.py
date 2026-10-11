"""Trusted Docker exec control plane; never invoked with project-supplied commands.

Only root has access to the policy directory and CAP_NET_ADMIN. Workloads run as
separate numeric UIDs without capabilities or access to this control state.
"""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from .egress_policy import EXECUTION_SLOTS, FIRST_PROXY_PORT, ExecutionNetworkPolicy
from .network_rules import BLOCKED_V4, TABLE, identity_rules, initialize_rules

ROOT = Path("/run/opsmesh-executions")
PROXY_USER = "proxy"


def resolvers() -> tuple[str, ...]:
    return tuple(
        line.split()[1]
        for line in Path("/etc/resolv.conf").read_text().splitlines()
        if line.startswith("nameserver ")
    )


def nft(transaction: str) -> None:
    subprocess.run(
        ["nft", "-f", "-"],
        input=transaction,
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )


def initialize() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Execution control requires root")
    proxy = pwd.getpwnam(PROXY_USER)
    ROOT.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(ROOT, 0, proxy.pw_gid)
    Path("/run/opsmesh-identities").mkdir(mode=0o755, exist_ok=True)
    for directory in ("/tmp/opsmesh-mcp", "/tmp/opsmesh-runs"):
        Path(directory).mkdir(mode=0o711, exist_ok=True)
        os.chmod(directory, 0o711)
    nft(initialize_rules(proxy.pw_uid, resolvers()))
    ports = "\n".join(
        f"http_port 127.0.0.1:{FIRST_PROXY_PORT + slot}" for slot in range(EXECUTION_SLOTS)
    )
    helper = (
        "external_acl_type execution ttl=0 negative_ttl=0 children-startup=2 children-max=8 "
        f"%>lp %>rd %>rP {Path(sys.executable).with_name('opsmesh-runtime-egress-helper')}"
    )
    configuration = f"""{ports}
pid_filename /run/squid/squid.pid
cache_effective_user proxy
cache_effective_group proxy
cache deny all
cache_mem 8 MB
pinger_enable off
cache_log /dev/null
access_log none
cache_store_log none
logfile_rotate 0
visible_hostname opsmesh-runtime
{helper}
acl permitted external execution
acl forbidden dst {" ".join(BLOCKED_V4)}
http_access deny forbidden
http_access allow permitted
http_access deny all
forwarded_for delete
"""
    Path("/run/squid").mkdir(mode=0o755, exist_ok=True)
    os.chown("/run/squid", proxy.pw_uid, proxy.pw_gid)
    Path("/run/squid/opsmesh.conf").write_text(configuration)


def policy_path(uid: int) -> Path:
    from .egress_policy import execution_port

    execution_port(uid)
    return ROOT / f"{uid}.json"


def kill_identity(uid: int) -> None:
    # Reap all descendants, including children that escaped their original process
    # group. A UID cannot be reused until its complete process tree is gone.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        found = False
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            try:
                if entry.stat().st_uid == uid:
                    found = True
                    os.kill(int(entry.name), sig)
            except (ProcessLookupError, FileNotFoundError):
                pass
        if not found:
            return
        time.sleep(0.1)
    for _ in range(20):
        remaining = []
        for entry in Path("/proc").iterdir():
            try:
                if entry.name.isdecimal() and entry.stat().st_uid == uid:
                    remaining.append(entry.name)
            except FileNotFoundError:
                continue
        if not remaining:
            return
        time.sleep(0.05)
    raise RuntimeError("Execution processes have not terminated")


def configure(value: dict) -> None:
    policy = ExecutionNetworkPolicy.parse(value)
    path = policy_path(policy.uid)
    if path.exists():
        current = ExecutionNetworkPolicy.parse(json.loads(path.read_bytes()))
        if current != policy:
            raise ValueError("Execution identity is already allocated")
    else:
        kill_identity(policy.uid)
        clear_private_directories(policy.uid)
    chain = f"execution_{policy.uid}"
    exists = (
        subprocess.run(
            ["nft", "list", "chain", "inet", TABLE, chain], capture_output=True, timeout=10
        ).returncode
        == 0
    )
    nft(identity_rules(policy, resolvers(), exists=exists))
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(policy.as_dict()))
    os.chmod(temporary, 0o640)
    os.chown(temporary, 0, pwd.getpwnam(PROXY_USER).pw_gid)
    temporary.replace(path)
    directory = Path(f"/run/opsmesh-identities/{policy.uid}")
    directory.mkdir(mode=0o555, exist_ok=True)
    marker = directory / "allocation"
    marker.write_text(str(policy.allocation_id))
    os.chmod(marker, 0o444)
    for parent in ("/tmp/opsmesh-mcp", "/tmp/opsmesh-runs"):
        private = Path(parent) / str(policy.uid)
        private.mkdir(mode=0o700, exist_ok=True)
        os.chown(private, policy.uid, policy.uid)


def revoke(value: dict) -> None:
    policy = ExecutionNetworkPolicy.parse(value)
    path = policy_path(policy.uid)
    if not path.exists():
        return
    current = ExecutionNetworkPolicy.parse(json.loads(path.read_bytes()))
    if current.allocation_id != policy.allocation_id:
        raise ValueError("Execution allocation identity does not match")
    # Remove network permissions before signalling. Existing connections are
    # filtered too; there is no output 'established accept' bypass.
    nft(
        identity_rules(
            ExecutionNetworkPolicy(policy.allocation_id, policy.uid, "none"), (), exists=True
        )
    )
    (Path(f"/run/opsmesh-identities/{policy.uid}") / "allocation").unlink(missing_ok=True)
    kill_identity(policy.uid)
    clear_private_directories(policy.uid)
    path.unlink()


def clear_private_directories(uid: int) -> None:
    for parent in ("/tmp/opsmesh-mcp", "/tmp/opsmesh-runs"):
        directory = Path(parent) / str(uid)
        if directory.exists():
            shutil.rmtree(directory)


def main() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Execution control requires root")
    if sys.argv[1:] == ["--initialize"]:
        initialize()
        proxy = pwd.getpwnam(PROXY_USER)
        os.setgroups([])
        os.setgid(proxy.pw_gid)
        os.setuid(proxy.pw_uid)
        os.execvp("squid", ["squid", "-N", "-f", "/run/squid/opsmesh.conf"])
    raw = sys.stdin.buffer.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("Execution control payload is too large")
    request = json.loads(raw)
    if set(request) != {"action", "execution"}:
        raise ValueError("Execution control request is invalid")
    with (ROOT / "control.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if request["action"] == "configure":
            configure(request["execution"])
        elif request["action"] == "revoke":
            revoke(request["execution"])
        else:
            raise ValueError("Unknown execution control action")


if __name__ == "__main__":
    main()
