"""Real kernel enforcement and command cleanup; enabled only by explicit opt-in."""

import asyncio
import json
import os

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.execution.api import CommandRequest, ExecutionSpec
from opsmesh.modules.execution.internal.docker import docker_control
from opsmesh.modules.execution.internal.service import DockerExecutionProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("OPSMESH_TEST_DOCKER_INTEGRATION") != "1",
        reason="Explicit Docker opt-in required",
    ),
]


async def create_session(path):
    return await DockerExecutionProvider().create(
        ExecutionSpec(
            workspace=path,
            provider="docker",
            image="python:3.13-slim",
            network="none",
            cpu=0.5,
            memory_mb=128,
            pids_limit=32,
        )
    )


async def test_real_docker_limits_files_network_and_timeout_cleanup(tmp_path):
    session = await create_session(tmp_path)
    container = session._container
    try:
        probe = await session.execute(
            CommandRequest(
                id="isolation",
                argv=(
                    "python",
                    "-c",
                    "import json,pathlib,os; p=pathlib.Path; "
                    "p('/workspace/result.txt').write_text('persistent'); "
                    "print(json.dumps({'memory':p('/sys/fs/cgroup/memory.max').read_text().strip(),"
                    "'pids':p('/sys/fs/cgroup/pids.max').read_text().strip(),"
                    "'cpu':p('/sys/fs/cgroup/cpu.max').read_text().strip(),"
                    "'interfaces':os.listdir('/sys/class/net')}))",
                ),
            )
        )
        values = json.loads(probe.stdout)
        assert probe.exit_code == 0 and values["memory"] == str(128 * 1024 * 1024)
        assert values["pids"] == "32" and values["cpu"] == "50000 100000"
        assert values["interfaces"] == ["lo"]
        assert (tmp_path / "result.txt").read_text() == "persistent"
        denied = await session.execute(
            CommandRequest(
                id="readonly",
                argv=("sh", "-c", "echo denied > /etc/opsmesh-test"),
            )
        )
        assert denied.exit_code != 0 and "Read-only" in denied.stderr
        with pytest.raises(OpsMeshError) as failure:
            await session.execute(
                CommandRequest(
                    id="timeout",
                    argv=("python", "-c", "import time;time.sleep(60)"),
                    timeout_seconds=0.3,
                )
            )
        assert failure.value.code == "EXECUTION_TIMEOUT"
        details = json.loads(await docker_control("inspect", container))[0]
        assert not details["State"]["Running"]
    finally:
        await session.close()
    with pytest.raises(OpsMeshError):
        await docker_control("inspect", container)


async def test_real_docker_cancellation_stops_remote_command_and_removes_container(tmp_path):
    session = await create_session(tmp_path)
    container = session._container
    try:
        pending = asyncio.create_task(
            session.execute(
                CommandRequest(
                    id="cancel",
                    argv=("python", "-c", "import time;time.sleep(60)"),
                )
            )
        )
        async with asyncio.timeout(10):
            while "cancel" not in session._processes:
                await asyncio.sleep(0.01)
        await session.cancel("cancel")
        with pytest.raises(OpsMeshError) as failure:
            await pending
        assert failure.value.code == "EXECUTION_CANCELLED"
        assert not json.loads(await docker_control("inspect", container))[0]["State"]["Running"]
    finally:
        await session.close()
    with pytest.raises(OpsMeshError):
        await docker_control("inspect", container)
