"""Resident acceptance exports, with no server imports or permanent host authority."""

import asyncio
import os

from opsmesh_sdk.extensions.operations import OperationResult, declare_operation
from opsmesh_sdk.extensions.package import ExtensionExports
from opsmesh_sdk.manifest import EffectKind, ExecutionPolicy, PackageManifest, Permission

ACTIVE = 0
PEAK = 0


def exports(context):
    @declare_operation(
        "probe",
        description="Resident concurrency probe",
        permissions=("host.state.read", "host.state.write"),
        effects=(EffectKind.WRITE,),
        timeout_seconds=30,
    )
    async def probe(arguments):
        global ACTIVE, PEAK
        ACTIVE += 1
        PEAK = max(PEAK, ACTIVE)
        try:
            await context.state.set(arguments["key"], {"pid": os.getpid()})
            await asyncio.sleep(arguments.get("delay", 0))
            return OperationResult(
                value={
                    "pid": os.getpid(),
                    "invocation_id": context.subject.invocation_id,
                    "peak": PEAK,
                    "active": ACTIVE,
                    "uid": os.geteuid(),
                },
                effects=(EffectKind.WRITE,),
            )
        finally:
            ACTIVE -= 1

    return ExtensionExports(
        PackageManifest(
            package_name="s14_managed",
            version="1",
            description="S14 resident acceptance",
            permissions=tuple(
                Permission(name=p, reason="Acceptance")
                for p in (
                    "host.state.read",
                    "host.state.write",
                )
            ),
            execution=ExecutionPolicy(mode="managed_worker", max_concurrency=2),
        ),
        operations=(probe,),
    )
