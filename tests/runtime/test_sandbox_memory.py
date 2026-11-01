"""Native memory request ownership and complete local SDK lifecycle."""

import io
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents import RunConfig, Runner, set_tracing_disabled
from agents.items import ModelResponse
from agents.models.interface import Model
from agents.sandbox import SandboxAgent, SandboxRunConfig
from agents.sandbox.capabilities import Memory
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from opsmesh.application.sandbox_bridge import archive_files
from opsmesh.framework.agents import AgentExecutionContext, AgentSpecification, SandboxConfiguration
from opsmesh.framework.agents.resources import AgentExecutionServices
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox import prepare_capabilities
from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_lifecycle import (
    ManagedSandboxSession,
)
from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_memory import (
    ManagedMemoryGeneration,
    MeteredMemoryModel,
)


def message(text):
    return ResponseOutputMessage(
        id="m",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


class ScriptedModel(Model):
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    async def get_response(self, *args, **kwargs):
        self.calls += 1
        value = self.outputs.pop(0)
        if isinstance(value, BaseException):
            raise value
        return ModelResponse(
            output=value,
            usage=Usage(requests=1, input_tokens=5, output_tokens=3, total_tokens=8),
            response_id=f"response-{self.calls}",
        )

    async def stream_response(self, *args, **kwargs):
        raise AssertionError("Memory never requests streaming")
        yield


def context(observations, authorizations):
    async def unused(*args):
        raise AssertionError("No platform tool invocation")

    async def observe(item):
        observations.append(item)

    async def authorize(profile):
        authorizations.append(profile)

    return AgentExecutionContext(
        "memory-run",
        "conversation",
        "principal",
        unused,
        services=AgentExecutionServices(observe_usage=observe, authorize_model_request=authorize),
    )


@pytest.mark.asyncio
async def test_native_memory_model_accounts_success_and_failed_unknown_requests():
    observations, authorizations = [], []
    delegate = ScriptedModel([[message("ok")], RuntimeError("transport failed")])
    model = MeteredMemoryModel(
        delegate, context(observations, authorizations), "model@1", "extract"
    )
    await model.get_response()
    with pytest.raises(RuntimeError):
        await model.get_response()
    assert authorizations == ["model@1", "model@1"]
    assert observations[0]["input_tokens"] == 5 and observations[0]["usage_available"]
    assert not observations[1]["usage_available"]
    assert len({row["call_id"] for row in observations}) == 2
    assert all(row["purpose"] == "memory_generation" for row in observations)


@pytest.mark.asyncio
async def test_native_memory_authorization_denied_before_request():
    observations, authorizations = [], []
    ctx = context(observations, authorizations)

    async def denied(profile):
        raise OpsMeshError("AUTHORIZATION_DENIED", "Not allowed")

    ctx.services.authorize_model_request = denied
    delegate = ScriptedModel([[message("unused")]])
    model = MeteredMemoryModel(delegate, ctx, "model@1", "extract")
    with pytest.raises(OpsMeshError):
        await model.get_response()
    assert delegate.calls == 0 and observations == []


@pytest.mark.asyncio
async def test_native_memory_archive_flushes_before_capture_and_cleanup():
    actions = []

    class Session:
        async def persist_workspace(self):
            actions.append("archive")
            return io.BytesIO(b"generated memory")

        async def aclose(self):
            actions.append("close")

    class Client:
        async def delete(self, session):
            actions.append("delete")

    class Memory:
        async def flush(self):
            actions.append("flush")

        def receipt(self):
            return {"status": "succeeded"}

        async def close_clients(self):
            actions.append("clients")

    manager = ManagedSandboxSession(Client(), Session(), 100)
    manager.memories.append(Memory())
    assert await manager.archive() == b"generated memory"
    await manager.close()
    assert actions == ["flush", "archive", "close", "delete", "clients"]


@pytest.mark.asyncio
async def test_native_memory_stage_errors_are_not_swallowed(monkeypatch):
    class Session:
        def register_pre_stop_hook(self, callback):
            self.callback = callback

    manager = ManagedMemoryGeneration(Session(), Memory(read=None), context([], []), 1)
    manager._rollout_files_by_rollout_id["r"] = "r.jsonl"
    manager._storage = SimpleNamespace(ensure_layout=AsyncMock())
    manager._process_rollout_file = AsyncMock(side_effect=RuntimeError("extract failed"))
    with pytest.raises(RuntimeError, match="extract failed"):
        await manager.flush()
    assert manager.status == "failed"
    with pytest.raises(OpsMeshError) as repeated:
        await manager.flush()
    assert repeated.value.code == "MEMORY_GENERATION_FAILED"

    second = ManagedMemoryGeneration(Session(), Memory(read=None), context([], []), 1)
    second._pending_phase_two_rollout_ids = ["r"]
    selected = SimpleNamespace(selected=["r"])
    second._storage = SimpleNamespace(
        build_phase_two_input_selection=AsyncMock(return_value=selected),
        rebuild_raw_memories=AsyncMock(return_value=True),
        write_phase_two_selection=AsyncMock(),
    )
    monkeypatch.setattr(
        "opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_memory.run_phase_two",
        AsyncMock(side_effect=RuntimeError("consolidate failed")),
    )
    with pytest.raises(RuntimeError, match="consolidate failed"):
        await second._run_phase_two()
    second._storage.write_phase_two_selection.assert_not_awaited()


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32" or os.getenv("OPSMESH_TEST_SANDBOX_INTEGRATION") != "1",
    reason="Explicit Unix native sandbox integration",
)
@pytest.mark.parametrize("provider", ["local", "docker"])
@pytest.mark.asyncio
async def test_real_native_memory_two_stages_persist_before_cleanup(provider):
    if provider == "docker" and not os.getenv("OPSMESH_TEST_DOCKER_INTEGRATION"):
        pytest.skip("Explicit Docker daemon required")
    set_tracing_disabled(True)
    configuration = SandboxConfiguration(
        provider=provider,
        trusted_local=provider == "local",
        options={"image": "python:3.13-slim", "network_mode": "none"}
        if provider == "docker"
        else {},
        capability_options=({"type": "memory", "read": None, "generate": {}},),
    )
    spec = AgentSpecification(
        id="native", name="Native", model_profile_ref="offline@1", sandbox=configuration
    )
    manager = await ManagedSandboxSession.open(configuration, (), ())
    observations, authorizations = [], []
    ctx = context(observations, authorizations)
    ctx.resources.sandbox_lifecycle = manager
    extraction = ScriptedModel(
        [
            [
                message(
                    json.dumps(
                        {
                            "rollout_slug": "lesson",
                            "rollout_summary": "A useful solution",
                            "raw_memory": "Use the documented API",
                        }
                    )
                )
            ]
        ]
    )
    consolidation = ScriptedModel(
        [
            [
                ResponseFunctionToolCall(
                    id="tool",
                    call_id="tool",
                    type="function_call",
                    name="exec_command",
                    arguments=json.dumps(
                        {
                            "cmd": "printf 'durable lesson' > memories/memory_summary.md",
                            "yield_time_ms": 1000,
                            "max_output_tokens": 1000,
                        }
                    ),
                )
            ],
            [message("memory saved")],
        ]
    )

    def resolver(stage_spec, execution_context):
        return extraction if stage_spec.id.endswith("-extract") else consolidation

    try:
        caps = await prepare_capabilities(spec, ctx, [], resolver)
        agent = SandboxAgent(
            name="Native", model=ScriptedModel([[message("business result")]]), capabilities=caps
        )
        await Runner.run(
            agent,
            "Learn this useful solution",
            run_config=RunConfig(
                sandbox=SandboxRunConfig(session=manager.session),
                group_id="conversation",
                tracing_disabled=True,
            ),
        )
        raw = await manager.archive()
        files, _ = archive_files(raw, max_members=100, max_bytes=1_000_000)
        content = {path: data for path, data, _ in files}
        assert content["memories/memory_summary.md"] == b"durable lesson"
        assert "memories/phase_two_selection.json" in content
        assert len(observations) == 3 and authorizations == ["offline@1"] * 3
        assert {row["stage"] for row in observations} == {"extract", "consolidate"}
        assert manager.memories[0].status == "succeeded"
        await manager.archive()
        assert len(observations) == 3
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_native_memory_identity_excludes_runtime_clients_and_usage():
    from collections.abc import Mapping

    from agents._run_state_agent_identity import _capability_identity_signature

    class RuntimeConnectionPool(Mapping):
        def __iter__(self):
            raise NotImplementedError("Runtime connection pools cannot be traversed")

        def __getitem__(self, key):
            raise KeyError(key)

        def __len__(self):
            return 1

    observations = []
    ctx = context(observations, [])
    ctx.extensions["live_docker_transport"] = RuntimeConnectionPool()
    ctx.extensions["credential"] = "never-serialize-runtime-secret"
    model = MeteredMemoryModel(ScriptedModel([[message("ok")]]), ctx, "model@1", "extract")
    memory = Memory.model_validate(
        {"generate": {"phase_one_model": model, "phase_two_model": model}}
    )
    before = json.dumps(_capability_identity_signature(memory), sort_keys=True)
    await model.get_response()
    after = json.dumps(_capability_identity_signature(memory), sort_keys=True)
    assert before == after and observations
    assert "model@1" in before and "never-serialize" not in before
