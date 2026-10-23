"""Await a one-shot Compose service through Docker's native container lifecycle."""

from __future__ import annotations

import argparse

from opsmesh_operator.commands import run_command


def wait_for_service_completion(command: list[str], *, service: str, timeout: int) -> None:
    import docker

    identifiers = run_command(
        [*command, "ps", "--all", "--quiet", service], timeout=timeout
    ).split()
    if len(identifiers) != 1:
        raise RuntimeError("Initialization requires exactly one Compose service container")
    client = docker.from_env(timeout=timeout)
    try:
        container = client.containers.get(identifiers[0])
        if container.labels.get("com.docker.compose.service") != service:
            raise RuntimeError("Initialization container does not belong to the requested service")
        result = container.wait(timeout=timeout, condition="not-running")
        if result.get("StatusCode") != 0 or result.get("Error"):
            raise RuntimeError("Compose service initialization failed")
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--service", default="temporal-namespace")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if command[:2] != ["docker", "compose"] or args.timeout <= 0:
        parser.error("A Docker Compose command and a positive timeout are required")
    wait_for_service_completion(command, service=args.service, timeout=args.timeout)


if __name__ == "__main__":
    main()
