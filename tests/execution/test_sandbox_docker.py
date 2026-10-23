"""Opt-in real Docker evidence for bounded transfer and cancellation acknowledgement."""

import asyncio
import base64
import os
from pathlib import Path
from uuid import uuid4

import pytest
from agents.sandbox.errors import WorkspaceArchiveReadError, WorkspaceReadNotFoundError

from opsmesh.execution.runtime.manifest import WORKING_DIRECTORIES
from opsmesh.execution.runtime.sandbox import (
    MAX_FILE_BYTES,
    SandboxBackend,
    SandboxCommandCancelled,
    read_file,
    write_file,
)
from opsmesh.execution.runtime.schemas import SandboxDirectory

pytestmark = pytest.mark.skipif(
    not os.environ.get("OPSMESH_TEST_SANDBOX_IMAGE"),
    reason="Requires a real test Docker daemon and an approved Python runtime image",
)


def test_nested_write_creates_parents_without_following_directory_symlinks(tmp_path):
    async def scenario():
        directory_id, environment_id, principal_id = uuid4(), uuid4(), uuid4()
        root = Path(os.environ.get("OPSMESH_TEST_SANDBOX_HOST_ROOT", str(tmp_path))) / str(
            directory_id
        )
        root.mkdir(parents=True, exist_ok=True)
        backend = SandboxBackend(lambda **_: SandboxDirectory(directory_id, root))
        native = await backend.create(
            image=os.environ["OPSMESH_TEST_SANDBOX_IMAGE"],
            labels={
                "opsmesh.environment": str(environment_id),
                "opsmesh.principal": str(principal_id),
                "opsmesh.test": f"nested-write:{uuid4()}",
            },
        )
        try:
            await backend.prepare_directory(native)
            await backend.initialize_directory(native)
            content = b"print(6 * 7)\n"
            encoded = base64.b64encode(content).decode()
            path = "new/project/main.py"
            assert await write_file(native, path, encoded) == len(content)
            assert base64.b64decode(await read_file(native, path)) == content
            result = await native.exec("python3", f"/workspace/{path}", shell=False)
            assert result.exit_code == 0
            assert result.stdout == b"42\n"
            setup = await native.exec(
                "sh",
                "-c",
                "mkdir -p /tmp/write-target && ln -s /tmp/write-target /workspace/escape",
                shell=False,
            )
            assert setup.exit_code == 0
            with pytest.raises(ValueError, match="safely"):
                await write_file(native, "escape/marker.txt", encoded)
            unchanged = await native.exec(
                "test", "!", "-e", "/tmp/write-target/marker.txt", shell=False
            )
            assert unchanged.exit_code == 0
        finally:
            await backend.execute(native, "close", {})
            backend.close()

    asyncio.run(scenario())


def test_native_manifest_preserves_existing_workspace_across_container_recreation(tmp_path):
    async def scenario():
        directory_id, principal_id = uuid4(), uuid4()
        root = Path(os.environ.get("OPSMESH_TEST_SANDBOX_HOST_ROOT", str(tmp_path))) / str(
            directory_id
        )
        (root / "skills").mkdir(parents=True)
        prompt = root / "skills" / "SKILL.md"
        prompt.write_text("User-owned skill instructions", encoding="utf-8")
        backend = SandboxBackend(lambda **_: SandboxDirectory(directory_id, root))
        try:
            for generation in range(2):
                native = await backend.create(
                    image=os.environ["OPSMESH_TEST_SANDBOX_IMAGE"],
                    labels={
                        "opsmesh.environment": str(uuid4()),
                        "opsmesh.principal": str(principal_id),
                        "opsmesh.test": f"manifest:{directory_id}:{generation}",
                    },
                )
                container_id = native.state.container_id
                try:
                    await backend.prepare_directory(native)
                    await backend.initialize_directory(native)
                    assert all((root / name).is_dir() for name in WORKING_DIRECTORIES)
                    assert prompt.read_text(encoding="utf-8") == "User-owned skill instructions"
                    result = await native.exec(
                        "python3",
                        "-I",
                        "-c",
                        "from pathlib import Path; p=Path('/workspace/files/result.txt'); "
                        "assert not p.exists() or p.read_text() == 'persistent'; "
                        "p.write_text('persistent'); "
                        "assert Path('/workspace/skills/SKILL.md').read_text() "
                        "== 'User-owned skill instructions'",
                        shell=False,
                    )
                    assert result.exit_code == 0, result.stderr
                    resumed = await backend.resume(backend.state(native))
                    assert resumed.state.container_id == container_id
                    await backend.initialize_directory(resumed)
                    assert (root / "files" / "result.txt").read_text() == "persistent"
                finally:
                    await backend.execute(native, "close", {})
                    assert (
                        await asyncio.to_thread(backend.client.get_container, container_id) is None
                    )
        finally:
            backend.close()

    asyncio.run(scenario())


def test_remote_read_is_bounded_before_transfer_and_rejects_symlinks_and_fifos(tmp_path):
    async def scenario():
        directory_id, environment_id, principal_id = uuid4(), uuid4(), uuid4()
        root = Path(os.environ.get("OPSMESH_TEST_SANDBOX_HOST_ROOT", str(tmp_path))) / str(
            directory_id
        )
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
            await backend.prepare_directory(native)
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
                with pytest.raises(WorkspaceArchiveReadError):
                    await read_file(observed, name)
            with pytest.raises(WorkspaceReadNotFoundError):
                await read_file(observed, "missing.txt")
            assert max(observed.sizes) == MAX_FILE_BYTES + 1
        finally:
            await backend.execute(native, "close", {})
            assert await asyncio.to_thread(backend.client.get_container, container_id) is None
            backend.close()

    asyncio.run(scenario())


def test_cancelled_native_command_finishes_before_ack_and_keeps_shared_environment(tmp_path):
    async def scenario():
        directory_id, environment_id, principal_id = uuid4(), uuid4(), uuid4()
        root = Path(os.environ.get("OPSMESH_TEST_SANDBOX_HOST_ROOT", str(tmp_path))) / str(
            directory_id
        )
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
            await backend.prepare_directory(native)
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
