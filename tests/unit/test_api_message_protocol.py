"""Exercise message envelopes, continuations, streaming, and cleanup."""

import json
from types import ModuleType
from typing import Annotated
from uuid import UUID, uuid4

import anyio
import httpx
import pytest

from opsmesh.adapters import Channel, Event
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.drivers import base as driver_state
from opsmesh.matchers import Matcher, MatcherSource, matchers
from opsmesh.params import Depends
from opsmesh.plugins import registry
from opsmesh.plugins.channels import Attachment, Message, MessageSegment
from opsmesh.plugins.channels.api import (
    APIChannel,
    APIRouter,
    HTTPError,
    IdentitySettings,
    MessageRequest,
    Settings,
    routing,
)
from opsmesh.plugins.model import Plugin, PluginStatus
from opsmesh.utils import flatten_exception_group


class ProtocolHarness:
    def __init__(self, monkeypatch):
        self.router = APIRouter()
        self.owners = {}
        monkeypatch.setenv("MESSAGE_TOKEN", "secret")
        monkeypatch.setenv("OTHER_MESSAGE_TOKEN", "other")
        monkeypatch.setattr(routing, "_router", self.router)
        monkeypatch.setattr(registry, "get_plugin", self.owners.get)
        monkeypatch.setattr(driver_state, "_driver", Driver(Env(), Config()))
        self.channel = APIChannel(
            Settings(
                enabled=False,
                identities=(
                    IdentitySettings(id="alice", token_ref="MESSAGE_TOKEN"),
                    IdentitySettings(id="bob", token_ref="OTHER_MESSAGE_TOKEN"),
                ),
            ),
            self.router,
        )

    def bind(self, handler, owner_id="support"):
        owner = Plugin(
            name=owner_id,
            module=ModuleType(owner_id),
            module_name=owner_id,
            manager=None,
            status=PluginStatus.READY,
        )
        self.owners[owner_id] = owner
        token = registry._current_plugin.set(owner)
        try:
            matcher = Matcher.new(
                type_="message",
                priority=10,
                block=True,
                handlers=[handler],
                source=MatcherSource(plugin_id=owner_id),
            )
            routing.message(handlers=(matcher,))
            return matcher
        finally:
            registry._current_plugin.reset(token)

    async def post(self, message="hello", **options):
        owner = options.pop("owner", "support")
        token = options.pop("token", "secret")
        body = {"message": message, **options.pop("fields", {})}
        return await self.client.post(
            f"/plugins/{owner}/messages",
            json=body,
            headers={"authorization": f"Bearer {token}", **options},
        )


@pytest.fixture
async def protocol(monkeypatch):
    saved = dict(matchers.items())
    matchers.clear()
    harness = ProtocolHarness(monkeypatch)
    await harness.channel.startup()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.channel.app),
        base_url="http://test",
    ) as client:
        harness.client = client
        try:
            yield harness
        finally:
            await harness.channel.shutdown()
            matchers.clear()
            matchers.update(saved)


def decode_events(response):
    return [
        (
            frame.splitlines()[0].removeprefix("event: "),
            json.loads(frame.splitlines()[1].removeprefix("data: ")),
        )
        for frame in response.text.split("\n\n")
        if frame
    ]


async def asgi_request(protocol, send, disconnect=None):
    content = json.dumps({"message": "hello"}).encode()
    pending = [{"type": "http.request", "body": content, "more_body": False}]

    async def receive():
        if pending:
            return pending.pop(0)
        if disconnect is not None:
            await disconnect.wait()
            return {"type": "http.disconnect"}
        await anyio.sleep_forever()

    await protocol.channel.app(
        {
            "type": "http",
            "method": "POST",
            "path": "/plugins/support/messages",
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "headers": [
                (b"authorization", b"Bearer secret"),
                (b"content-type", b"application/json"),
                (b"accept", b"text/event-stream"),
            ],
        },
        receive,
        send,
    )


@pytest.mark.anyio
async def test_public_message_binding_has_one_fixed_path_and_body(protocol):
    async def ignored():
        return {"ignored": True}

    protocol.bind(ignored)
    response = await protocol.post()
    payload = response.json()
    assert response.status_code == 200
    assert str(UUID(payload["conversation_id"])) == payload["conversation_id"]
    assert payload == {
        "conversation_id": payload["conversation_id"],
        "status": "completed",
        "messages": [],
        "error": None,
    }
    assert (
        await protocol.client.post("/plugins/support/chat")
    ).status_code == 404
    with pytest.raises(TypeError):
        routing.message("/chat", handlers=())
    with pytest.raises(TypeError):
        routing.message(handlers=(), body=MessageRequest)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "fields",
    [
        {"user": "forged"},
        {"roles": ["admin"]},
        {"session_id": "sdk-id"},
        {"conversation_id": "D250759B-8D9A-4C3A-A1AF-397292FC835D"},
        {"conversation_id": "d250759b8d9a4c3aa1af397292fc835d"},
        {"conversation_id": "invalid"},
        {"conversation_id": 42},
    ],
)
async def test_message_protocol_rejects_untrusted_or_noncanonical_fields(
    protocol,
    fields,
):
    async def unexpected():
        pytest.fail("Invalid input reached the framework")

    protocol.bind(unexpected)
    assert (await protocol.post(fields=fields)).status_code == 422


@pytest.mark.anyio
@pytest.mark.parametrize(
    "message",
    [
        [{"type": "text", "text": "   "}],
        [{"type": "data", "value": {"admin": True}}],
        [{"type": "file", "attachment_id": "0" * 32}],
    ],
)
async def test_invalid_segments_are_rejected_before_dispatch(protocol, message):
    async def unexpected():
        pytest.fail("Invalid segments reached the framework")

    protocol.bind(unexpected)
    assert (await protocol.post(message)).status_code in (404, 422)


@pytest.mark.anyio
async def test_conversation_scope_separates_users_plugins_and_windows(protocol):
    seen = []

    async def remember(event: Event):
        seen.append(event.get_session_id())

    protocol.bind(remember)
    protocol.bind(remember, "other_plugin")
    conversation = str(uuid4())
    fields = {"conversation_id": conversation}
    await protocol.post(fields=fields)
    await protocol.post(fields=fields)
    await protocol.post(fields=fields, token="other")
    await protocol.post(fields=fields, owner="other_plugin")
    await protocol.post(fields={"conversation_id": str(uuid4())})
    assert seen[0] == seen[1]
    assert len(set(seen)) == 4


@pytest.mark.anyio
async def test_multiple_sends_and_finish_preserve_complete_message_order(
    protocol,
):
    async def answer(matcher: Matcher):
        await matcher.send("first")
        await matcher.send(
            Message(
                [
                    MessageSegment.text("selected"),
                    MessageSegment.structured({"session_id": "session-123"}),
                ]
            )
        )
        await matcher.finish("last")

    root = protocol.bind(answer)

    @root.handle()
    async def unexpected():
        pytest.fail("finish must end this matcher")

    response = await protocol.post()
    assert response.json()["messages"] == [
        {"content": [{"type": "text", "text": "first"}], "data": None},
        {
            "content": [{"type": "text", "text": "selected"}],
            "data": {"session_id": "session-123"},
        },
        {"content": [{"type": "text", "text": "last"}], "data": None},
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["handler", "cleanup"])
async def test_failures_preserve_sent_messages_without_leaking_details(
    protocol,
    failure,
):
    async def resource():
        yield "resource"
        if failure == "cleanup":
            raise RuntimeError("private dependency details")

    async def answer(
        matcher: Matcher, value: Annotated[object, Depends(resource)]
    ):
        await matcher.send("already sent")
        if failure == "handler":
            raise RuntimeError("private handler details")

    protocol.bind(answer)
    response = await protocol.post()
    payload = response.json()
    assert response.status_code == 500
    assert payload["status"] == "failed"
    assert payload["messages"][0]["content"][0]["text"] == "already sent"
    assert payload["error"] == {
        "code": "500",
        "message": "internal server error",
    }
    assert "private" not in response.text
    assert not protocol.channel._active_conversations


@pytest.mark.anyio
async def test_safe_handler_error_keeps_its_http_status(protocol):
    async def denied():
        raise HTTPError(403, "request is not permitted")

    protocol.bind(denied)
    response = await protocol.post()
    assert response.status_code == 403
    assert response.json()["error"] == {
        "code": "403",
        "message": "request is not permitted",
    }


@pytest.mark.anyio
async def test_attachment_bytes_are_materialized_before_dependency_cleanup(
    protocol,
):
    stages = []

    async def resource():
        yield "resource"
        stages.append("dependency closed")

    async def answer(
        event: Event,
        matcher: Matcher,
        value: Annotated[object, Depends(resource)],
    ):
        async def read():
            assert not stages
            stages.append("read")
            return b"report"

        attachment = Attachment.create("report.txt", "text/plain")
        event.attachments.add(attachment, read)
        await matcher.send(MessageSegment.attachment(attachment))

    protocol.bind(answer)
    response = await protocol.post()
    url = response.json()["messages"][0]["content"][0]["download_url"]
    assert stages == ["read", "dependency closed"]
    download = await protocol.client.get(
        url,
        headers={
            "authorization": "Bearer secret",
        },
    )
    assert download.content == b"report"


@pytest.mark.anyio
async def test_json_stream_aggregates_within_the_callers_scope(protocol):
    stages = []

    async def source():
        try:
            yield "one"
            yield " two"
        finally:
            stages.append("source closed")

    async def answer(channel: Channel, event: Event):
        await channel.call("stream", event=event, source=source())
        assert stages == ["source closed"]
        stages.append("caller continued")

    protocol.bind(answer)
    response = await protocol.post()
    assert response.json()["messages"] == [
        {
            "content": [{"type": "text", "text": "one two"}],
            "data": None,
        }
    ]
    assert stages == ["source closed", "caller continued"]


@pytest.mark.anyio
async def test_sse_keeps_messages_separate_and_finishes_after_cleanup(protocol):
    stages = []

    async def resource():
        yield "resource"
        stages.append("dependency closed")

    async def source():
        try:
            yield "one"
            yield " two"
        finally:
            stages.append("source closed")

    async def answer(
        channel: Channel,
        event: Event,
        matcher: Matcher,
        value: Annotated[object, Depends(resource)],
    ):
        await matcher.send("before")
        await channel.call("stream", event=event, source=source())
        stages.append("caller continued")
        await matcher.finish("after")

    protocol.bind(answer)
    response = await protocol.post(accept="text/event-stream")
    events = decode_events(response)
    assert [name for name, _ in events] == [
        "message",
        "message_start",
        "message_delta",
        "message_delta",
        "message_end",
        "message",
        "done",
    ]
    identifier = events[1][1]["message_id"]
    assert all(item["message_id"] == identifier for _, item in events[2:5])
    assert [item["sequence"] for _, item in events[2:4]] == [1, 2]
    assert len({events[index][1]["message_id"] for index in (0, 1, 5)}) == 3
    assert events[-1][1]["status"] == "completed"
    assert stages == ["source closed", "caller continued", "dependency closed"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure", ["increment", "source_cleanup", "dependency"]
)
async def test_sse_failures_end_with_safe_error_and_no_success_done(
    protocol,
    failure,
):
    stages = []

    async def resource():
        try:
            yield "resource"
        finally:
            stages.append("dependency closed")
            if failure == "dependency":
                raise RuntimeError("private dependency failure")

    async def source():
        try:
            yield "partial"
            if failure == "increment":
                raise RuntimeError("private source failure")
        finally:
            stages.append("source closed")
            if failure == "source_cleanup":
                raise RuntimeError("private source cleanup failure")

    async def answer(
        channel: Channel,
        event: Event,
        value: Annotated[object, Depends(resource)],
    ):
        await channel.call("stream", event=event, source=source())

    protocol.bind(answer)
    response = await protocol.post(accept="text/event-stream")
    events = decode_events(response)
    names = [name for name, _ in events]
    assert names[-1] == "error"
    assert "done" not in names
    assert ("message_end" in names) is (failure == "dependency")
    assert "private" not in response.text
    assert stages == ["source closed", "dependency closed"]


@pytest.mark.anyio
async def test_same_scope_is_busy_until_dependency_cleanup_finishes(protocol):
    entered, release = anyio.Event(), anyio.Event()
    first = []

    async def resource():
        yield "resource"
        entered.set()
        await release.wait()

    async def answer(value: Annotated[object, Depends(resource)]):
        return None

    protocol.bind(answer)
    fields = {"conversation_id": str(uuid4())}

    async def request():
        first.append(await protocol.post(fields=fields))

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(request)
        await entered.wait()
        blocked = await protocol.post(fields=fields)
        assert blocked.status_code == 409
        release.set()
    assert first[0].status_code == 200
    assert (await protocol.post(fields=fields)).status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize("control", ["pause", "reject"])
async def test_http_continuation_uses_conversation_scope_and_fresh_dependencies(
    protocol,
    control,
):
    opened, closed = [], []

    async def resource():
        value = len(opened)
        opened.append(value)
        try:
            yield value
        finally:
            closed.append(value)

    async def prompt(
        matcher: Matcher,
        event: Event,
        value: Annotated[object, Depends(resource)],
    ):
        if control == "pause":
            await matcher.pause("next")
        if "first" not in matcher.state:
            matcher.state["first"] = event.get_plaintext()
            await matcher.reject("retry")
        await matcher.finish("resumed")

    root = protocol.bind(prompt)

    @root.handle()
    async def resumed(
        matcher: Matcher, value: Annotated[object, Depends(resource)]
    ):
        await matcher.finish("resumed")

    fields = {"conversation_id": str(uuid4())}
    first = await protocol.post(fields=fields)
    assert first.json()["status"] == "waiting"
    assert closed == opened
    second = await protocol.post(fields=fields)
    assert second.json()["status"] == "completed"
    assert second.json()["messages"][0]["content"][0]["text"] == "resumed"
    assert len(opened) == 2 and closed == opened


@pytest.mark.anyio
async def test_sse_done_is_sent_only_after_dependency_finalizers_finish(
    protocol,
):
    ended, release = anyio.Event(), anyio.Event()
    stages, sent = [], []

    async def resource():
        try:
            yield "resource"
        finally:
            await release.wait()
            stages.append("dependency closed")

    async def answer(
        matcher: Matcher, value: Annotated[object, Depends(resource)]
    ):
        await matcher.finish("answer")

    async def send(message):
        frame = message.get("body", b"")
        if b"event: message\n" in frame:
            ended.set()
        if b"event: done\n" in frame:
            assert stages == ["dependency closed"]
        sent.append(frame)

    protocol.bind(answer)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(asgi_request, protocol, send)
        await ended.wait()
        assert not any(b"event: done\n" in frame for frame in sent)
        release.set()
    assert sum(b"event: done\n" in frame for frame in sent) == 1


@pytest.mark.anyio
async def test_sse_disconnect_cancels_source_and_closes_dependencies(protocol):
    disconnected = anyio.Event()
    stages, frames = [], []

    async def resource():
        try:
            yield "resource"
        finally:
            stages.append("dependency closed")

    async def source():
        try:
            yield "partial"
            await anyio.sleep_forever()
        finally:
            stages.append("source closed")

    async def answer(
        channel: Channel,
        event: Event,
        value: Annotated[object, Depends(resource)],
    ):
        await channel.call("stream", event=event, source=source())

    async def send(message):
        frame = message.get("body", b"")
        frames.append(frame)
        if b"event: message_delta\n" in frame:
            disconnected.set()

    protocol.bind(answer)
    with anyio.fail_after(2):
        await asgi_request(protocol, send, disconnected)
    assert stages == ["source closed", "dependency closed"]
    assert not any(b"event: done\n" in frame for frame in frames)
    assert not any(b"event: message_end\n" in frame for frame in frames)
    assert not protocol.channel._active_conversations
    assert not protocol.owners["support"].calls


@pytest.mark.anyio
async def test_sse_buffer_applies_backpressure_to_text_source(protocol):
    blocked, release = anyio.Event(), anyio.Event()
    consumed = []
    protocol.channel.settings = protocol.channel.settings.model_copy(
        update={
            "stream_buffer_size": 1,
        }
    )

    async def source():
        for index in range(20):
            consumed.append(index)
            yield "part"

    async def answer(channel: Channel, event: Event):
        await channel.call("stream", event=event, source=source())

    async def send(message):
        if b"event: message_delta\n" in message.get("body", b""):
            blocked.set()
            await release.wait()

    protocol.bind(answer)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(asgi_request, protocol, send)
        await blocked.wait()
        await anyio.sleep(0.01)
        assert len(consumed) <= 3
        release.set()
    assert len(consumed) == 20


@pytest.mark.anyio
@pytest.mark.parametrize("limit", ["count", "bytes"])
async def test_message_output_limits_fail_safely_and_release_admission(
    protocol,
    limit,
):
    protocol.channel.settings = protocol.channel.settings.model_copy(
        update={
            "max_output_messages": 1 if limit == "count" else 128,
            "max_output_bytes": 32 if limit == "bytes" else 1024,
        }
    )

    async def answer(matcher: Matcher):
        await matcher.send("first")
        await matcher.send("second")

    protocol.bind(answer)
    response = await protocol.post()
    payload = response.json()
    assert response.status_code == 500
    assert payload["status"] == "failed"
    assert len(payload["messages"]) == (1 if limit == "count" else 0)
    assert not protocol.channel._active_conversations


@pytest.mark.anyio
async def test_nonstream_timeout_returns_failed_envelope_after_cleanup(
    protocol,
):
    closed = []
    protocol.channel.settings = protocol.channel.settings.model_copy(
        update={
            "request_timeout_seconds": 0.1,
        }
    )

    async def resource():
        try:
            yield "resource"
        finally:
            closed.append(True)

    async def answer(
        matcher: Matcher, value: Annotated[object, Depends(resource)]
    ):
        await matcher.send("partial")
        await anyio.sleep_forever()

    protocol.bind(answer)
    response = await protocol.post()
    assert response.status_code == 504
    assert response.json()["status"] == "failed"
    assert response.json()["messages"][0]["content"][0]["text"] == "partial"
    assert closed == [True]


@pytest.mark.anyio
async def test_sse_is_not_limited_by_nonstream_request_timeout(protocol):
    protocol.channel.settings = protocol.channel.settings.model_copy(
        update={
            "request_timeout_seconds": 0.001,
        }
    )

    async def answer(matcher: Matcher):
        await anyio.sleep(0.01)
        await matcher.finish("answer")

    protocol.bind(answer)
    events = decode_events(await protocol.post(accept="text/event-stream"))
    assert [name for name, _ in events] == ["message", "done"]


@pytest.mark.anyio
async def test_dependency_finalizer_cannot_send_a_new_business_message(
    protocol,
):
    async def resource(channel: Channel, event: Event):
        yield "resource"
        await channel.send(event, "too late")

    async def answer(
        matcher: Matcher, value: Annotated[object, Depends(resource)]
    ):
        await matcher.finish("answer")

    protocol.bind(answer)
    response = await protocol.post()
    assert response.status_code == 500
    assert response.json()["status"] == "failed"
    assert len(response.json()["messages"]) == 1


@pytest.mark.anyio
async def test_stop_waits_for_active_embedded_requests_before_releasing_channel(
    protocol,
):
    entered, release = anyio.Event(), anyio.Event()

    async def answer(matcher: Matcher):
        entered.set()
        await release.wait()
        await matcher.finish("answer")

    protocol.bind(answer)
    completed = []

    async def post():
        completed.append(await protocol.post())

    async def stop():
        await protocol.channel.shutdown()
        completed.append("stopped")

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(post)
        await entered.wait()
        tasks.start_soon(stop)
        await anyio.sleep(0)
        assert not protocol.channel.started
        assert protocol.channel._connected
        release.set()
    assert completed[-1] == "stopped"
    assert completed[0].json()["messages"][0]["content"][0]["text"] == "answer"


@pytest.mark.anyio
async def test_sse_transport_failure_cancels_source_and_closes_dependencies(
    protocol,
):
    stages = []

    async def resource():
        try:
            yield "resource"
        finally:
            stages.append("dependency closed")

    async def source():
        try:
            yield "partial"
            await anyio.sleep_forever()
        finally:
            stages.append("source closed")

    async def answer(
        channel: Channel,
        event: Event,
        value: Annotated[object, Depends(resource)],
    ):
        await channel.call("stream", event=event, source=source())

    async def send(message):
        if b"event: message_delta\n" in message.get("body", b""):
            raise OSError("transport closed")

    protocol.bind(answer)
    with anyio.fail_after(2), pytest.raises(ExceptionGroup) as failure:
        await asgi_request(protocol, send)
    assert any(
        isinstance(error, OSError)
        for error in flatten_exception_group(
            failure.value,
        )
    )
    assert stages == ["source closed", "dependency closed"]
    assert not protocol.owners["support"].calls
    assert not protocol.channel._active_conversations


@pytest.mark.anyio
async def test_source_cleanup_timeout_still_attempts_dependency_cleanup(
    protocol,
    monkeypatch,
):
    from opsmesh.plugins.channels import sending

    monkeypatch.setattr(sending, "_STREAM_CLEANUP_TIMEOUT", 0.01)
    stages = []

    class Source:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("private source failure")

        async def aclose(self):
            stages.append("source close attempted")
            await anyio.sleep_forever()

    async def resource():
        try:
            yield "resource"
        finally:
            stages.append("dependency closed")

    async def answer(
        channel: Channel,
        event: Event,
        value: Annotated[object, Depends(resource)],
    ):
        await channel.call("stream", event=event, source=Source())

    protocol.bind(answer)
    response = await protocol.post(accept="text/event-stream")
    assert [name for name, _ in decode_events(response)] == [
        "message_start",
        "error",
    ]
    assert stages == ["source close attempted", "dependency closed"]
    assert "private" not in response.text
