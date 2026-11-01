"""Explicit online updates run outside the application process and its service account."""

import argparse
from pathlib import Path

import httpx

from opsmesh.maintenance.updates import ReleaseUpdateService


async def execute_update(arguments: argparse.Namespace) -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        updater = ReleaseUpdateService(client)
        release = await updater.check(arguments.release_version)
        if arguments.update_action == "check":
            print(release.model_dump_json())
        elif arguments.update_action == "download":
            print(await updater.download(release, Path(arguments.directory)))
        else:
            await updater.install(
                release,
                Path(arguments.directory),
                instance=arguments.instance,
                mode=arguments.mode,
                no_start=arguments.no_start,
            )
