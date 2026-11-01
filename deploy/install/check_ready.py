"""Check the native listen address, or wait for the local HTTP readiness check."""

import argparse
import socket
import time
from contextlib import ExitStack
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from opsmesh.bootstrap.settings import OpsMeshSettings


def check_bind(settings: OpsMeshSettings) -> int:
    """Reject an occupied address before switching the installed application."""
    try:
        addresses = socket.getaddrinfo(
            settings.host or None,
            settings.port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
            flags=socket.AI_PASSIVE,
        )
        with ExitStack() as sockets:
            seen = set()
            for family, _, _, _, address in addresses:
                if (family, address) in seen:
                    continue
                seen.add((family, address))
                sockets.enter_context(socket.create_server(address, family=family))
    except OSError as error:
        print(f"Cannot bind OpsMesh to {settings.host}:{settings.port}: {error}")
        return 1
    print(f"Listen address is available: {settings.host}:{settings.port}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-bind", action="store_true", help="Check the address before startup"
    )
    arguments = parser.parse_args()
    settings = OpsMeshSettings.from_environment()
    if arguments.check_bind:
        return check_bind(settings)
    host = settings.host
    if host in {"0.0.0.0", ""}:
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host:
        host = f"[{host}]"
    url = f"http://{host}:{settings.port}/ready"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    print("OpsMesh is ready.")
                    return 0
        except (HTTPError, URLError, TimeoutError):
            pass
        time.sleep(1)
    print("OpsMesh did not become ready within 60 seconds; inspect service logs.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
