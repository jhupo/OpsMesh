"""Responses transport and metered session compaction exercise installed SDK implementations."""

import json
from types import SimpleNamespace

import httpx
import pytest
from agents import RunContextWrapper
from openai import AsyncOpenAI

from opsmesh.framework.agents import AgentSpecification, SessionCompactionConfiguration
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.runtime.internal.backends.openai_agents.backend import OpenAIAgentsBackend
from opsmesh.modules.runtime.internal.backends.openai_agents.compaction import build_session
from opsmesh.modules.runtime.internal.backends.openai_agents.models import resolve_profile_model
from opsmesh.modules.runtime.internal.extensions import RuntimeExtensionRegistry
from opsmesh.modules.runtime.internal.history import ModelHistory
from tests.runtime.test_openai_backend import ScriptedModel, context, message


@pytest.mark.asyncio
async def test_real_compaction_session_meters_and_atomically_replaces_history(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    store = VersionedRecordStore(database, "history")
    await database.initialize()
    history = ModelHistory(store, "session")
    await history.add_items(
        [{"role": "user", "content": "long input"}, message("long answer").model_dump(mode="json")]
    )
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": f"cmp{len(sent)}",
                "created_at": 0,
                "object": "response.compaction",
                "output": [
                    {"type": "compaction", "id": "cmpitem1", "encrypted_content": "opaque-summary"}
                ],
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 4,
                    "total_tokens": 16,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 1},
                },
            },
        )

    client = AsyncOpenAI(
        api_key="offline",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        max_retries=0,
    )
    ctx = context()
    observations = []
    admissions = []
    ctx.services.update(
        model_history=history,
        compaction_client=client,
        observe_usage=observations.append,
        authorize_model_request=admissions.append,
    )
    spec = AgentSpecification(
        id="a",
        name="A",
        model="gpt-4.1",
        model_profile_ref="profile@1",
        session_compaction=SessionCompactionConfiguration(threshold=1),
    )
    session = build_session(spec, ctx, SimpleNamespace(model="gpt-4.1"))
    wrapper = RunContextWrapper(ctx)
    try:
        await session.run_compaction({"force": True}, wrapper=wrapper)
        assert (await history.get_items())[0]["type"] == "compaction"
        assert admissions == ["profile@1"] and len(observations) == 1
        assert (
            observations[0]["total_tokens"] == 16
            and observations[0]["model_profile_ref"] == "profile@1"
        )
        assert wrapper.usage.total_tokens == 16
        assert sent[0]["model"] == "gpt-4.1" and "input" in sent[0]
        observations.clear()
        model = ScriptedModel([[message("next answer")]])
        backend = OpenAIAgentsBackend(RuntimeExtensionRegistry(), model_resolver=lambda *_: model)
        outcome = await backend.run(spec, "next question", ctx)
        assert outcome.usage.total_tokens == 21
        assert len(observations) == 2 and len(sent) == 2
        assert (await history.get_items())[-1]["type"] == "compaction"
    finally:
        await client.close()
        await database.close()


@pytest.mark.asyncio
async def test_compaction_cas_rejects_concurrent_history_change(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'cas.db'}")
    store = VersionedRecordStore(database, "history")
    await database.initialize()
    first, second = ModelHistory(store, "session"), ModelHistory(store, "session")
    try:
        await first.add_items([{"role": "user", "content": "original"}])
        await first.get_items()
        await second.add_items([{"role": "user", "content": "newer"}])
        with pytest.raises(OpsMeshError) as error:
            await first._atomic_replace_items([{"role": "user", "content": "summary"}])
        assert error.value.code == "CONFLICT"
        assert len(await second.get_items()) == 2
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_websocket_profile_builds_actual_sdk_transport_and_closes():
    from agents.models.openai_responses import OpenAIResponsesWSModel

    ctx = context()
    ctx.services["model_profile"] = {
        "id": "profile",
        "revision": 1,
        "api": "responses",
        "model": "gpt-4.1",
        "base_url": "http://127.0.0.1:1/v1",
        "secret_ref": "env:offline",
    }
    ctx.services["resolve_secret"] = lambda ref: "offline"
    clients = []
    model, _ = await resolve_profile_model(
        AgentSpecification(
            id="a", name="A", model_profile_ref="profile@1", model_transport="websocket"
        ),
        ctx,
        clients,
    )
    try:
        assert isinstance(model, OpenAIResponsesWSModel)
        assert clients[0].max_retries == 0
    finally:
        await model.close()
        await clients[0].close()


@pytest.mark.asyncio
async def test_real_websocket_round_trip_on_loopback():
    from websockets.asyncio.server import serve

    frames = []

    async def handler(socket):
        frame = json.loads(await socket.recv())
        frames.append(frame)
        response = {
            "id": "resp_ws_1",
            "created_at": 0,
            "model": "gpt-4.1",
            "object": "response",
            "output": [message("websocket result").model_dump(mode="json")],
            "status": "completed",
            "tools": [],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "usage": {
                "input_tokens": 3,
                "output_tokens": 2,
                "total_tokens": 5,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        }
        await socket.send(
            json.dumps(
                {
                    "type": "response.created",
                    "sequence_number": 0,
                    "response": {**response, "status": "in_progress", "output": []},
                }
            )
        )
        await socket.send(
            json.dumps(
                {
                    "type": "response.output_item.done",
                    "sequence_number": 1,
                    "output_index": 0,
                    "item": response["output"][0],
                }
            )
        )
        await socket.send(
            json.dumps({"type": "response.completed", "sequence_number": 2, "response": response})
        )
        await socket.wait_closed()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        ctx = context()
        ctx.services["model_profile"] = {
            "id": "profile",
            "revision": 1,
            "api": "responses",
            "model": "gpt-4.1",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "secret_ref": "env:offline",
        }
        ctx.services["resolve_secret"] = lambda ref: "offline"
        result = await OpenAIAgentsBackend(RuntimeExtensionRegistry()).run(
            AgentSpecification(
                id="a", name="A", model_profile_ref="profile@1", model_transport="websocket"
            ),
            "hi",
            ctx,
        )
    assert result.output == "websocket result" and result.usage.total_tokens == 5
    assert frames[0]["type"] == "response.create"
