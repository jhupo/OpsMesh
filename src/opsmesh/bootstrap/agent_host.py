"""Compose native SDK execution inside an approved Runtime process."""

import asyncio
import logging
import sys

from opsmesh.bootstrap.providers import build_agent_runtime_registry
from opsmesh.runtime.agent_host.main import execute


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    asyncio.run(execute(build_agent_runtime_registry()))


if __name__ == "__main__":
    main()
