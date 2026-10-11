"""Root-only relay into the preloaded Runtime execution service.

The relay sends public allocation metadata. Run inputs and credentials travel
directly to the unprivileged child; the preloader never consumes those frames.
"""

import json
import os
import selectors
import socket
import sys

SOCKET_PATH = "/run/opsmesh-agent-pool.sock"
MAX_ADMISSION_BYTES = 4096


def main() -> None:
    if os.geteuid() != 0:
        raise PermissionError("Runtime pool admission requires the trusted controller")
    allocation, uid, working_dir = sys.argv[1:]
    admission = (
        json.dumps(
            {"allocation_id": allocation, "uid": int(uid), "working_dir": working_dir},
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    if len(admission) > MAX_ADMISSION_BYTES:
        raise ValueError("Runtime pool admission exceeds its limit")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.connect(SOCKET_PATH)
        connection.sendall(admission)
        acknowledged = bytearray()
        while not acknowledged.endswith(b"\n"):
            chunk = connection.recv(1)
            if not chunk or len(acknowledged) >= MAX_ADMISSION_BYTES:
                raise ValueError("Invalid Runtime pool acknowledgment")
            acknowledged.extend(chunk)
        if json.loads(acknowledged) != {"admitted": True}:
            os.write(sys.stdout.fileno(), acknowledged)
            return
        with selectors.DefaultSelector() as selector:
            selector.register(sys.stdin.fileno(), selectors.EVENT_READ, "input")
            selector.register(connection, selectors.EVENT_READ, "output")
            while True:
                for key, _ in selector.select():
                    if key.data == "input":
                        data = os.read(sys.stdin.fileno(), 65536)
                        if not data:
                            connection.shutdown(socket.SHUT_WR)
                            selector.unregister(sys.stdin.fileno())
                        else:
                            connection.sendall(data)
                    else:
                        data = connection.recv(65536)
                        if not data:
                            return
                        view = memoryview(data)
                        while view:
                            view = view[os.write(sys.stdout.fileno(), view) :]


if __name__ == "__main__":
    main()
