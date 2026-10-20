"""Completed package effects replay without spawning another process."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from opsmesh.extensions.sandbox import PackageSandbox
from opsmesh.shared.errors import ForbiddenError


def test_completed_package_replay_ignores_only_remaining_deadline():
    asyncio.run(_completed_package_replay())


async def _completed_package_replay():
    environments = AsyncMock()
    environments.claim.return_value = (False, {"value": "already completed"})
    sandbox = PackageSandbox(environments)
    arguments = {
        "environment_id": uuid4(),
        "principal_id": uuid4(),
        "configuration_id": uuid4(),
        "operation_id": "extension:fixed",
        "artifact": "approved-package:immutable",
        "deadline": datetime.now(UTC) + timedelta(minutes=1),
        "host": AsyncMock(),
        "memory_mb": 128,
        "cpus": 1.0,
    }
    envelope = {"subject": {"invocation_id": str(uuid4())}, "deadline_seconds": 60}
    assert await sandbox.execute(**arguments, envelope=envelope) == {"value": "already completed"}
    first = environments.claim.call_args.kwargs
    await sandbox.execute(**arguments, envelope={**envelope, "deadline_seconds": 30})
    second = environments.claim.call_args.kwargs
    assert first["fingerprint"] == second["fingerprint"]
    assert second["configuration_id"] == arguments["configuration_id"]
    await sandbox.execute(**arguments, envelope={**envelope, "payload": {"changed": True}})
    assert environments.claim.call_args.kwargs["fingerprint"] != first["fingerprint"]
    environments.container_id.assert_not_awaited()
    environments.complete.assert_not_awaited()


def test_denied_environment_never_reaches_stdio_transport():
    asyncio.run(_denied_environment())


async def _denied_environment():
    environments = AsyncMock()
    environments.claim.side_effect = ForbiddenError("Grant revoked")
    with pytest.raises(ForbiddenError):
        await PackageSandbox(environments).execute(
            environment_id=uuid4(),
            principal_id=uuid4(),
            configuration_id=None,
            operation_id="extension:denied",
            artifact="approved-package:immutable",
            envelope={"subject": {"invocation_id": str(uuid4())}},
            deadline=datetime.now(UTC) + timedelta(minutes=1),
            host=AsyncMock(),
            memory_mb=128,
            cpus=1.0,
        )
    environments.container_id.assert_not_awaited()
    environments.complete.assert_not_awaited()
