"""Persistent environments receive manifest and skill entries through the native SDK."""

from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from agents.sandbox import ExecResult, Manifest, SandboxAgent
from agents.sandbox.capabilities import Skills
from agents.sandbox.entries import Dir, File
from agents.sandbox.errors import WorkspaceArchiveReadError, WorkspaceReadNotFoundError

from opsmesh.agents.assembly import AgentBuild
from opsmesh.agents.manager import AgentDefinition
from opsmesh.agents.skills import SkillInstallation
from opsmesh.execution.preparation import freeze_agent
from opsmesh.execution.runtime.preparation import agent_manifest
from opsmesh.execution.runtime.sandbox import AgentSandboxSession, AgentSandboxState, read_file
from opsmesh.shared.errors import InvalidArgumentError


def test_manifest_includes_static_skill_files_from_all_agents():
    agents = {
        name: SandboxAgent(
            name=name,
            model="configured-model",
            default_manifest=Manifest(entries={f"{name}.txt": File(content=name.encode())}),
            capabilities=[Skills(from_=Dir(), skills_path="skills")],
        )
        for name in ("root", "helper")
    }
    installations = {
        name: (
            SkillInstallation(f"skills/{name}", Dir(children={"SKILL.md": File(content=b"skill")})),
        )
        for name in agents
    }
    manifest = agent_manifest(AgentBuild(agents["root"], agents, installations))
    files = {str(path).replace("\\", "/"): entry for path, entry in manifest.iter_entries()}
    assert files["root.txt"].content == b"root"
    assert files["helper.txt"].content == b"helper"
    assert files["skills/root/SKILL.md"].content == b"skill"
    assert files["skills/helper/SKILL.md"].content == b"skill"


def test_conflicting_files_fail_before_sandbox_io():
    agents = {
        name: SandboxAgent(
            name=name,
            model="configured-model",
            default_manifest=Manifest(entries={"config.txt": File(content=name.encode())}),
        )
        for name in ("root", "helper")
    }
    with pytest.raises(ValueError, match="Conflicting shared workspace"):
        agent_manifest(AgentBuild(agents["root"], agents, {}))


@pytest.mark.asyncio
async def test_session_start_applies_manifest_without_deleting_environment():
    client = Mock()
    reference = AgentSandboxState(session_id=uuid4(), environment_id=uuid4())
    session = AgentSandboxSession(client, reference)
    session.apply_manifest = AsyncMock()
    await session.start()
    session.apply_manifest.assert_awaited_once_with()
    await session.shutdown()
    client.delete.assert_not_called()
    client.backend.close.assert_not_called()


@pytest.mark.asyncio
async def test_repeated_steps_have_distinct_replay_occurrences():
    client = Mock(execute=AsyncMock(return_value={"stdout": "", "stderr": "", "exit_code": 0}))
    session = AgentSandboxSession(
        client, AgentSandboxState(session_id=uuid4(), environment_id=uuid4())
    )
    await session._exec_internal("mkdir", "-p", Path("skills"))
    await session._exec_internal("mkdir", "-p", Path("skills"))
    assert [call.kwargs["occurrence"] for call in client.execute.await_args_list] == [1, 2]


@pytest.mark.asyncio
async def test_cached_session_restarts_occurrence_numbers_for_activity_retry():
    client = Mock(execute=AsyncMock(return_value={"stdout": "", "stderr": "", "exit_code": 0}))
    client.operation_scope.side_effect = ["activity:1", "activity:1", "activity:2"]
    session = AgentSandboxSession(
        client, AgentSandboxState(session_id=uuid4(), environment_id=uuid4())
    )
    for _ in range(3):
        await session._exec_internal("mkdir", "-p", "skills")
    assert [call.kwargs["occurrence"] for call in client.execute.await_args_list] == [1, 2, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exit_code", "expected"), [(66, WorkspaceReadNotFoundError), (77, WorkspaceArchiveReadError)]
)
async def test_file_reads_preserve_missing_and_permission_error_contracts(exit_code, expected):
    native = Mock(
        exec=AsyncMock(return_value=ExecResult(stdout=b"", stderr=b"", exit_code=exit_code))
    )
    with pytest.raises(expected):
        await read_file(native, "rollouts/first.jsonl")


@pytest.mark.asyncio
async def test_borrowed_session_preserves_confirmed_missing_file_result():
    client = Mock(execute=AsyncMock(return_value={"missing": True}))
    session = AgentSandboxSession(
        client, AgentSandboxState(session_id=uuid4(), environment_id=uuid4())
    )
    with pytest.raises(WorkspaceReadNotFoundError):
        await session.read(Path("rollouts/first.jsonl"))


def test_persistent_instance_rejects_user_override_before_sandbox_io():
    definition = AgentDefinition.model_validate(
        {"id": "root", "config": {"type": "sandbox", "name": "Root", "run_as": "1000"}}
    )
    with pytest.raises(InvalidArgumentError, match="run_as is unsupported"):
        freeze_agent("root", lambda _: definition)
