"""Preload SDKs once, then fork fresh identity-isolated execution processes.

Only this single-threaded supervisor remains privileged. It never receives run
inputs, credentials or SDK callbacks. Children inherit immutable module pages,
drop privileges irreversibly and create their own event loop and SDK clients.
"""

import asyncio
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

from opsmesh_runtime.agent_pool_client import MAX_ADMISSION_BYTES, SOCKET_PATH
from opsmesh_runtime.egress_policy import EXECUTION_SLOTS, ExecutionNetworkPolicy, execution_port

from opsmesh.bootstrap.providers import build_agent_runtime_registry
from opsmesh.runtime.agent_host.main import execute


def _admission(connection: socket.socket) -> tuple[ExecutionNetworkPolicy, Path]:
    _, uid, _ = struct.unpack(
        "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    )
    if uid != 0:
        raise PermissionError("Untrusted Runtime pool admission")
    connection.settimeout(2)
    # Do not read ahead: the next bytes belong exclusively to the execution child.
    raw = bytearray()
    while not raw.endswith(b"\n"):
        chunk = connection.recv(1)
        if not chunk or len(raw) >= MAX_ADMISSION_BYTES:
            raise ValueError("Invalid Runtime pool admission")
        raw.extend(chunk)
    value = json.loads(raw)
    if set(value) != {"allocation_id", "uid", "working_dir"}:
        raise ValueError("Invalid Runtime pool admission fields")
    execution_port(value["uid"])
    policy = ExecutionNetworkPolicy.parse(
        json.loads(Path(f"/run/opsmesh-executions/{value['uid']}.json").read_bytes())
    )
    allocation = UUID(value["allocation_id"])
    marker = Path(f"/run/opsmesh-identities/{policy.uid}/allocation")
    if policy.uid != value["uid"] or policy.allocation_id != allocation:
        raise PermissionError("Runtime allocation does not match")
    if UUID(str(marker.readlink())) != allocation:
        raise PermissionError("Runtime allocation has been revoked")
    directory = Path(value["working_dir"]).resolve(strict=True)
    if not directory.is_dir() or directory.stat().st_uid != policy.uid:
        raise PermissionError("Runtime workspace belongs to another identity")
    connection.settimeout(None)
    return policy, directory


def _child(connection: socket.socket, policy: ExecutionNetworkPolicy, directory: Path) -> None:
    os.setsid()
    os.umask(0o077)
    os.dup2(connection.fileno(), 0)
    os.dup2(connection.fileno(), 1)
    # Unstructured provider diagnostics never enter the Runtime container log.
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), 2)
    for descriptor in list(Path("/proc/self/fd").iterdir()):
        number = int(descriptor.name)
        if number > 2:
            with suppress(OSError):
                os.close(number)
    os.setgroups([])
    os.setgid(policy.uid)
    os.setuid(policy.uid)
    if os.getuid() != policy.uid or os.geteuid() != policy.uid:
        raise PermissionError("Runtime child failed to drop privileges")
    marker = Path(f"/run/opsmesh-identities/{policy.uid}/allocation")
    if UUID(str(marker.readlink())) != policy.allocation_id:
        raise PermissionError("Runtime allocation has been revoked")
    os.chdir(directory)
    private = f"/tmp/opsmesh-runs/{policy.uid}"
    proxy = f"http://127.0.0.1:{execution_port(policy.uid)}" if policy.mode == "restricted" else ""
    environment = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED")
        if key in os.environ
    }
    environment.update(
        HOME=private,
        TMPDIR=private,
        HTTP_PROXY=proxy,
        HTTPS_PROXY=proxy,
        ALL_PROXY=proxy,
        http_proxy=proxy,
        https_proxy=proxy,
        all_proxy=proxy,
        NO_PROXY="",
        no_proxy="",
    )
    os.environ.clear()
    os.environ.update(environment)
    sys.stdin = os.fdopen(0, "r", encoding="utf-8", closefd=False)
    sys.stdout = os.fdopen(1, "w", encoding="utf-8", buffering=1, closefd=False)
    sys.stderr = os.fdopen(2, "w", encoding="utf-8", buffering=1, closefd=False)
    asyncio.run(execute(build_agent_runtime_registry()))


def serve(proxy: subprocess.Popen[bytes], capacity: int) -> None:
    if os.geteuid() != 0 or not 1 <= capacity <= EXECUTION_SLOTS:
        raise ValueError("Invalid Runtime supervisor identity or capacity")
    # Fork only before SDK clients, event loops or trace exporter threads exist.
    if threading.active_count() != 1 or len(list(Path("/proc/self/task").iterdir())) != 1:
        raise RuntimeError("Runtime preloader must remain single-threaded")
    stopping = False
    children: dict[int, int] = {}

    def stop(signum: int, frame: Any) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    socket_path = Path(SOCKET_PATH)
    socket_path.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(SOCKET_PATH)
        socket_path.chmod(0o600)
        listener.listen(capacity)
        listener.settimeout(0.25)
        print(json.dumps({"event": "runtime.agent_pool.ready", "capacity": capacity}), flush=True)
        try:
            while not stopping:
                for pid in tuple(children):
                    if os.waitpid(pid, os.WNOHANG)[0]:
                        children.pop(pid)
                if proxy.poll() is not None:
                    raise RuntimeError("Runtime egress proxy exited")
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                with connection:
                    try:
                        policy, directory = _admission(connection)
                        # A child can finish while accept() waits. Reap again at
                        # admission so its UID cannot be falsely reported busy.
                        for child_pid in tuple(children):
                            if os.waitpid(child_pid, os.WNOHANG)[0]:
                                children.pop(child_pid)
                        if len(children) >= capacity or policy.uid in children.values():
                            raise RuntimeError("Runtime execution capacity or identity is busy")
                        pid = os.fork()
                        if pid == 0:
                            signal.signal(signal.SIGTERM, signal.SIG_DFL)
                            signal.signal(signal.SIGINT, signal.SIG_DFL)
                            try:
                                _child(connection, policy, directory)
                            except BaseException as error:
                                response = {
                                    "type": "error",
                                    "error": {
                                        "kind": "runtime",
                                        "code": "sdk_pool_child_failed",
                                        "message": type(error).__name__,
                                        "failure_location": [
                                            {
                                                "module": Path(frame.filename).name,
                                                "line": frame.lineno,
                                                "function": frame.name,
                                            }
                                            for frame in traceback.extract_tb(error.__traceback__)[
                                                -3:
                                            ]
                                        ],
                                    },
                                }
                                with suppress(OSError):
                                    os.write(1, json.dumps(response).encode() + b"\n")
                                os._exit(1)
                            os._exit(0)
                        children[pid] = policy.uid
                        connection.sendall(b'{"admitted":true}\n')
                    except Exception as error:
                        response = {
                            "type": "error",
                            "error": {
                                "kind": "runtime",
                                "code": "sdk_pool_admission_failed",
                                "message": type(error).__name__,
                            },
                        }
                        with suppress(OSError):
                            connection.sendall(json.dumps(response).encode() + b"\n")
        finally:
            socket_path.unlink(missing_ok=True)
            for pid in children:
                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    with suppress(ProcessLookupError):
                        os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 3
            while children and time.monotonic() < deadline:
                for pid in tuple(children):
                    if os.waitpid(pid, os.WNOHANG)[0]:
                        children.pop(pid)
                time.sleep(0.02)
            for pid in children:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    with suppress(ProcessLookupError):
                        os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            proxy.terminate()
            try:
                proxy.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait()
