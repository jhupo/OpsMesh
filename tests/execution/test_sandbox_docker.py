"""Opt-in real Docker evidence for bounded transfer and cancellation acknowledgement."""

import asyncio
import base64
import os
from pathlib import Path
from uuid import uuid4

import pytest

from opsmesh.orchestration.runtime.contracts import SandboxDirectory
from opsmesh.orchestration.runtime.files import MAX_FILE_BYTES, read_file
from opsmesh.orchestration.runtime.sandbox import SandboxBackend, SandboxCommandCancelled

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPSMESH_TEST_SANDBOX_IMAGE"),
    reason="Requires a real test Docker daemon and an approved Python runtime image",
)


def test_remote_read_is_bounded_before_transfer_and_rejects_symlinks_and_fifos(tmp_path):
    async def scenario():
        directory_id, environment_id, principal_id = uuid4(), uuid4(), uuid4()
        root = Path(os.environ.get("OPSMESH_TEST_SANDBOX_HOST_ROOT", str(tmp_path)))
        root.mkdir(parents=True, exist_ok=True)
        backend = SandboxBackend(lambda **_: SandboxDirectory(directory_id, root))
        native = await backend.create(
            image=os.environ["OPSMESH_TEST_SANDBOX_IMAGE"],
            labels={
                "opsmesh.environment": str(environment_id),
                "opsmesh.principal": str(principal_id),
                "opsmesh.test": f"s7-bounded-read:{uuid4()}",
            },
        )
        container_id = native.state.container_id
        try:
            await native.start()
            state = backend.state(native)
            resumed = await backend.resume(state)
            assert resumed.state.container_id == container_id
            native = resumed
            setup = await native.exec(
                "python3",
                "-I",
                "-c",
                "from pathlib import Path; import os; p=Path('/workspace'); "
                "(p/'huge.bin').touch(); "
                "f=(p/'huge.bin').open('wb'); f.truncate(1_000_000_000); f.close(); "
                f"(p/'exact.bin').write_bytes(b'x'*{MAX_FILE_BYTES}); "
                "(p/'empty.bin').touch(); "
                "(p/'escape').symlink_to('/etc/passwd'); "
                "(p/'directory-link').symlink_to('/etc', target_is_directory=True); "
                "os.mkfifo(p/'fifo')",
                shell=False,
                timeout=20,
            )
            assert setup.exit_code == 0

            class Observed:
                sizes = []

                async def exec(self, *command, **kwargs):
                    result = await native.exec(*command, **kwargs)
                    self.sizes.append(len(result.stdout))
                    return result

            observed = Observed()
            with pytest.raises(ValueError, match="transfer limit"):
                await read_file(observed, "huge.bin")
            assert observed.sizes == [MAX_FILE_BYTES + 1]
            assert base64.b64decode(await read_file(observed, "exact.bin")) == b"x" * MAX_FILE_BYTES
            assert await read_file(observed, "empty.bin") == ""
            for name in ("escape", "directory-link/passwd", "fifo"):
                with pytest.raises(ValueError, match="read safely"):
                    await read_file(observed, name)
            assert max(observed.sizes) == MAX_FILE_BYTES + 1
        finally:
            await backend.execute(native, "close", {})
            assert await asyncio.to_thread(backend.client.get_container, container_id) is None
            backend.close()

    asyncio.run(scenario())


def test_cancelled_native_command_finishes_before_ack_and_keeps_shared_environment(tmp_path):
    async def scenario():
        directory_id, environment_id, principal_id = uuid4(), uuid4(), uuid4()
        root = Path(os.environ.get("OPSMESH_TEST_SANDBOX_HOST_ROOT", str(tmp_path)))
        root.mkdir(parents=True, exist_ok=True)
        backend = SandboxBackend(lambda **_: SandboxDirectory(directory_id, root))
        native = await backend.create(
            image=os.environ["OPSMESH_TEST_SANDBOX_IMAGE"],
            labels={
                "opsmesh.environment": str(environment_id),
                "opsmesh.principal": str(principal_id),
                "opsmesh.test": f"s7-cancel:{uuid4()}",
            },
        )
        container_id = native.state.container_id
        try:
            await native.start()
            command = asyncio.create_task(
                backend.execute(
                    native,
                    "command",
                    {
                        "command": [
                            "python3",
                            "-I",
                            "-c",
                            "from pathlib import Path; import time; "
                            "Path('/workspace/started').touch(); time.sleep(2); "
                            "Path('/workspace/completed').touch(); print('done')",
                        ]
                    },
                )
            )
            async with asyncio.timeout(15):
                while True:
                    probe = await native.exec("test", "-f", "/workspace/started", shell=False)
                    if probe.exit_code == 0:
                        break
                    await asyncio.sleep(0.05)
            command.cancel()
            await asyncio.sleep(0.1)
            assert not command.done()
            with pytest.raises(SandboxCommandCancelled) as completed:
                await command
            assert completed.value.result["exit_code"] == 0
            assert completed.value.result["stdout"] == "done\n"
            probe = await native.exec("test", "-f", "/workspace/completed", shell=False)
            assert probe.exit_code == 0
            assert native.state.container_id == container_id
            assert await asyncio.to_thread(backend.client.get_container, container_id) is not None
        finally:
            await backend.execute(native, "close", {})
            assert await asyncio.to_thread(backend.client.get_container, container_id) is None
            backend.close()

    asyncio.run(scenario())
