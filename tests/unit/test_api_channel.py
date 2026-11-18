"""Check the consumer-owned HTTP connection and message contract."""

import json
from types import ModuleType
from typing import Annotated

import anyio
import httpx
import pytest

from opsmesh.adapters import Channel, Event
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.matchers import Matcher, MatcherSource, matchers
from opsmesh.messages import Message
from opsmesh.params import Context, Depends, EventMessage
from opsmesh.plugins import registry
from opsmesh.plugins.channels import (
    Message as ChannelMessage,
)
from opsmesh.plugins.channels import (
    MessageSegment,
    UserContext,
)
from opsmesh.plugins.channels.api import (
    APIAdapter,
    APIConnectionSettings,
    APIMessageEvent,
)
from opsmesh.plugins.manager import PluginManager
from opsmesh.plugins.model import Plugin, PluginStatus


def plugin_owner(name: str, *, status: PluginStatus = PluginStatus.READY):
    return Plugin(
        name=name,
        module=ModuleType(name),
        module_name=name,
        manager=PluginManager(),
        status=status,
    )


def structured_reply(value):
    return ChannelMessage(
        [
            MessageSegment.text("Result"),
            MessageSegment.structured(value),
        ]
    )


def message_body(message="hello", *, user="user", **profile):
    return {"user": {"id": user, **profile}, "message": message}


def scope(method: str = "POST", path: str = "/", *, body: bytes = b""):
    headers = [(b"content-type", b"application/json")] if body else []
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": headers,
        "scheme": "http",
        "server": ("testserver", 80),
    }, [
        {"type": "http.request", "body": body, "more_body": False},
    ]


def body_of(messages) -> bytes:
    return b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )


class APIHarness:
    """Declare HTTP connections through the real adapter contract."""

    def __init__(self, monkeypatch):
        self.driver = Driver(Env(), Config())
        self.driver.register_adapter(APIAdapter)
        self.adapter = self.driver.get_adapter(APIAdapter)
        self.owners = {}
        monkeypatch.setattr(registry, "_plugins", self.owners)
        monkeypatch.setattr(
            registry, "_module_plugins",
            {plugin.module_name: plugin for plugin in registry._plugins.values()},
        )
        self.channels = {}
        self.matchers = []

    def owner(self, name="support", **options):
        plugin = plugin_owner(name, **options)
        self.owners[name] = plugin
        registry._module_plugins[plugin.module_name] = plugin
        return plugin

    async def connect(self, name="support", connection="main", **options):
        owner = self.owners.get(name) or self.owner(name)
        token = registry._current_plugin.set(owner)
        try:
            channel = self.adapter.declare(
                APIConnectionSettings(enabled=False, **options),
                name=connection,
            )
        finally:
            registry._current_plugin.reset(token)
        await channel.prepare()
        channel.mark_ready()
        self.channels[(name, connection)] = channel
        return channel

    def bind(self, handler, owner_id="support", **options):
        matcher = Matcher.new(
            type_="message",
            priority=10,
            block=True,
            handlers=[handler],
            source=MatcherSource(plugin_id=owner_id),
            **options,
        )
        self.matchers.append(matcher)
        return matcher

    async def post(self, channel, owner="support", message="hello", **profile):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=channel.app),
            base_url="http://test",
        ) as client:
            return await client.post(
                f"/plugins/{owner}/messages",
                json=message_body(message, **profile),
            )


@pytest.fixture
async def api(monkeypatch):
    saved = dict(matchers.items())
    matchers.clear()
    harness = APIHarness(monkeypatch)
    try:
        yield harness
    finally:
        for matcher in harness.matchers:
            matcher.destroy()
        for channel in harness.channels.values():
            if channel.state != "closed":
                await channel.close()
        for owner in tuple(harness.owners.values()):
            while owner.activation_cleanups:
                owner.activation_cleanups.pop()()
        matchers.clear()
        matchers.update(saved)


async def request(app, *, method="POST", path="/", body=b""):
    request_scope, queued = scope(method, path, body=body)
    frames = []

    async def receive():
        if queued:
            return queued.pop(0)
        await anyio.sleep_forever()

    async def send(frame):
        frames.append(frame)

    await app(request_scope, receive, send)
    return frames


def test_connection_settings_reject_business_identity_configuration():
    with pytest.raises(ValueError, match="Extra inputs"):
        APIConnectionSettings(identities=[])


@pytest.mark.anyio
@pytest.mark.parametrize("question", ["/api_probe", "/api_probe hello"])
async def test_api_message_uses_framework_command_and_caller_context(
    api, question
):
    from opsmesh.params import CommandArg
    from opsmesh.rules import command

    async def handler(
        channel: Channel,
        event: Event,
        message: Annotated[Message, EventMessage()],
        argument: Annotated[Message, CommandArg()],
        caller: Annotated[UserContext, Context()],
    ):
        assert event.get_user_id() == caller.id
        assert event.get_type() == "message"
        assert message.extract_plain_text() == question
        assert len(message) == 1
        await channel.send(
            event,
            structured_reply(
                {
                    "argument": argument.extract_plain_text(),
                    "id": caller.id,
                    "roles": sorted(caller.roles),
                    "groups": sorted(caller.groups),
                }
            ),
        )

    with registry.plugin_scope(api.owner()):
        api.bind(handler, rule=command("api_probe", force_whitespace=True))
    channel = await api.connect()
    response = await api.post(
        channel,
        message=question,
        roles=["agent"],
        groups=["support"],
    )
    assert response.status_code == 200
    data = response.json()["messages"][0]["data"]
    assert data["argument"] == question.removeprefix("/api_probe").strip()
    assert json.loads(data["id"]) == ["user", "api:support:main"]
    assert data["roles"] == ["agent"]
    assert data["groups"] == ["support"]


@pytest.mark.anyio
async def test_api_channel_replies_with_a_framework_message(api):
    async def handler(
        channel: Channel,
        event: Event,
        message: Annotated[Message, EventMessage()],
    ):
        await channel.send(event, message)

    api.bind(handler)
    channel = await api.connect()
    response = await api.post(channel, message="hello")
    assert response.status_code == 200
    payload = response.json()
    assert payload["messages"] == [
        {
            "message_id": payload["messages"][0]["message_id"],
            "content": [{"type": "text", "text": "hello"}],
            "data": None,
        }
    ]


@pytest.mark.anyio
async def test_upload_message_and_download_share_connection_scope(api):
    async def handler(channel: Channel, event: APIMessageEvent):
        parts = event.get_message()
        assert [part.type for part in parts] == ["text", "file"]
        data = await event.attachments.read(parts[1].data["attachment_id"])
        await channel.send(
            event,
            structured_reply(
                {"text": parts.extract_plain_text(), "file": data.decode()}
            ),
        )

    api.bind(handler)
    channel = await api.connect()
    other = await api.connect("other")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=channel.app),
        base_url="http://test",
    ) as client:
        upload = await client.post(
            "/attachments/support?name=notes.txt",
            content=b"hello",
            headers={"content-type": "text/plain"},
        )
        assert upload.status_code == 201
        attachment_id = upload.json()["id"]
        path = f"/attachments/support/{attachment_id}"
        assert (await client.get(path)).content == b"hello"
        response = await client.post(
            "/plugins/support/messages",
            json=message_body(
                [
                    {"type": "text", "text": "read"},
                    {"type": "file", "attachment_id": attachment_id},
                ]
            ),
        )
        assert response.json()["messages"][0]["data"] == {
            "text": "read",
            "file": "hello",
        }

    other_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=other.app),
        base_url="http://test",
    )
    async with other_client as client:
        assert (await client.get(path)).status_code == 404
        assert (
            await client.post("/plugins/support/messages", json=message_body())
        ).status_code == 404

    api.owners["support"].status = PluginStatus.LOADED
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=channel.app),
        base_url="http://test",
    ) as client:
        assert (await client.get(path)).status_code == 503
    api.owners["support"].status = PluginStatus.READY
    await channel.close()
    assert not channel.uploads._uploads


@pytest.mark.anyio
@pytest.mark.parametrize(
    "request_body",
    [
        {},
        {"user": {"id": "user"}},
        {"user": {"id": ""}, "message": "hello"},
        {"user": {"id": "user"}, "message": ""},
        {"user": {"id": "user"}, "message": "  "},
        {"user": {"id": "user"}, "message": []},
        {"user": {"id": "user"}, "message": {"text": "hello"}},
        {"user": {"id": "forged"}, "message": "hello", "roles": ["admin"]},
    ],
)
async def test_message_connection_rejects_invalid_contract(api, request_body):
    async def handler():
        raise AssertionError("Invalid messages must not reach a matcher")

    api.bind(handler)
    channel = await api.connect()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=channel.app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/plugins/support/messages", json=request_body
        )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_message_handler_return_is_not_an_implicit_reply(api):
    async def handler():
        return {"ignored": True}

    api.bind(handler)
    channel = await api.connect()
    response = await api.post(channel)
    assert response.status_code == 200
    assert response.json()["messages"] == []
    assert response.json()["status"] == "completed"


@pytest.mark.anyio
async def test_message_handler_sends_multiple_complete_messages(api):
    async def handler(channel: Channel, event: Event):
        await channel.send(event, "first")
        await channel.send(event, "second")

    api.bind(handler)
    channel = await api.connect()
    response = await api.post(channel)
    assert response.status_code == 200
    assert [
        item["content"][0]["text"] for item in response.json()["messages"]
    ] == ["first", "second"]


@pytest.mark.anyio
async def test_image_only_api_request_stays_a_framework_message(api):
    from opsmesh.plugins.channels import Attachment

    async def handler(channel: Channel, event: Event):
        await channel.send(
            event,
            structured_reply(
                {
                    "type": event.get_type(),
                    "parts": [part.type for part in event.get_message()],
                }
            ),
        )

    api.bind(handler)
    channel = await api.connect()
    image = channel.uploads.put(
        channel.self_id,
        Attachment.create("image.png", "image/png"),
        b"image-content",
    )
    response = await api.post(
        channel,
        message=[{"type": "image", "attachment_id": image.id}],
    )
    assert response.json()["messages"][0]["data"] == {
        "type": "message",
        "parts": ["image"],
    }


@pytest.mark.anyio
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("spec", ["2.0", "2.4"])
async def test_message_transport_retains_owner_after_handler_cleanup(
    api, streamed, spec
):
    from opsmesh.plugins.channels import Delivery
    from opsmesh.plugins.invocation import _current_call
    from opsmesh.plugins.registry import get_current_plugin

    owner = api.owner()
    closed, observed = [], []
    cleaned = anyio.Event()

    async def resource():
        try:
            yield "ready"
        finally:
            closed.append(True)
            cleaned.set()

    async def observe(receipt):
        assert get_current_plugin() is owner
        assert owner.calls
        call = _current_call.get()
        import asyncio

        assert asyncio.current_task() in call.tasks
        assert closed == [True]
        observed.append(receipt.status)

    async def handler(
        channel: Channel,
        event: APIMessageEvent,
        value: Annotated[str, Depends(resource)],
    ):
        await channel.send(event, "reply", delivery=Delivery(observe))

    api.bind(handler)
    channel = await api.connect()
    request_scope, queued = scope(
        path="/plugins/support/messages",
        body=json.dumps(message_body()).encode(),
    )
    if streamed:
        request_scope["headers"].append((b"accept", b"text/event-stream"))
    request_scope["asgi"] = {"version": "3.0", "spec_version": spec}

    async def receive():
        if queued:
            return queued.pop(0)
        await anyio.sleep_forever()

    async def send(frame):
        if frame["type"] == "http.response.body" and frame.get("body"):
            await cleaned.wait()
            assert owner.calls
            assert closed == [True]
            owner.status = PluginStatus.STOPPING
            if b"reply" in frame["body"]:
                assert not observed

    with anyio.fail_after(5):
        await channel.app(request_scope, receive, send)
    assert observed == ["written"]
    assert not owner.calls
    assert not channel._active_conversations


@pytest.mark.anyio
@pytest.mark.parametrize("streamed", [False, True])
async def test_message_execution_timeout_applies_to_json_and_sse(api, streamed):
    closed = []

    async def handler():
        try:
            await anyio.sleep_forever()
        finally:
            closed.append(True)

    api.bind(handler)
    channel = await api.connect(request_timeout_seconds=0.02)
    request_scope, queued = scope(
        path="/plugins/support/messages",
        body=json.dumps(message_body()).encode(),
    )
    if streamed:
        request_scope["headers"].append((b"accept", b"text/event-stream"))
    frames = []

    async def receive():
        if queued:
            return queued.pop(0)
        await anyio.sleep_forever()

    async def send(frame):
        frames.append(frame)

    with anyio.fail_after(2):
        await channel.app(request_scope, receive, send)
    assert closed == [True]
    assert b"request timed out" in body_of(frames)
    if not streamed:
        assert frames[0]["status"] == 504
    assert not channel._active_conversations


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path", ["/plugins/support/messages", "/attachments/support"]
)
async def test_slow_body_releases_plugin_admission(api, path):
    async def handler():
        pytest.fail("An incomplete body must not reach the handler")

    api.bind(handler)
    channel = await api.connect(request_timeout_seconds=0.02)
    request_scope, _ = scope(path=path)
    frames = []

    async def receive():
        await anyio.sleep_forever()

    async def send(frame):
        frames.append(frame)

    with anyio.fail_after(2):
        await channel.app(request_scope, receive, send)
    assert frames[0]["status"] == 504
    assert not api.owners["support"].calls


@pytest.mark.anyio
@pytest.mark.parametrize("streamed", [False, True])
async def test_body_and_handler_share_one_deadline(api, streamed):
    async def handler(channel: Channel, event: Event):
        await anyio.sleep(0.25)
        await channel.send(event, "too late")

    api.bind(handler)
    channel = await api.connect(request_timeout_seconds=0.4)
    request_scope, queued = scope(
        path="/plugins/support/messages",
        body=json.dumps(message_body()).encode(),
    )
    if streamed:
        request_scope["headers"].append((b"accept", b"text/event-stream"))
    frames = []

    async def receive():
        if queued:
            await anyio.sleep(0.25)
            return queued.pop(0)
        await anyio.sleep_forever()

    async def send(frame):
        frames.append(frame)

    with anyio.fail_after(2):
        await channel.app(request_scope, receive, send)
    assert b"request timed out" in body_of(frames)
    assert b"too late" not in body_of(frames)
    assert not api.owners["support"].calls
    assert not channel._active_conversations


@pytest.mark.anyio
@pytest.mark.parametrize("streamed", [False, True])
async def test_permission_error_is_safe_in_json_and_sse(api, streamed):
    async def handler():
        raise PermissionError("private resource and credential details")

    api.bind(handler)
    channel = await api.connect()
    request_scope, queued = scope(
        path="/plugins/support/messages",
        body=json.dumps(message_body()).encode(),
    )
    if streamed:
        request_scope["headers"].append((b"accept", b"text/event-stream"))
    frames = []

    async def receive():
        if queued:
            return queued.pop(0)
        await anyio.sleep_forever()

    async def send(frame):
        frames.append(frame)

    await channel.app(request_scope, receive, send)
    assert b"request is not permitted" in body_of(frames)
    assert b"private resource" not in body_of(frames)
    assert b"403" in body_of(frames)
    assert not api.owners["support"].calls


@pytest.mark.anyio
async def test_connection_serves_only_its_owner_paths(api):
    async def handler():
        pytest.fail("Foreign requests reached a matcher")

    api.bind(handler)
    channel = await api.connect()
    frames = await request(
        channel.app,
        path="/plugins/other/messages",
        body=json.dumps(message_body()).encode(),
    )
    assert frames[0]["status"] == 404
    assert not api.owners["support"].calls


@pytest.mark.anyio
async def test_fixed_connection_observes_plugin_state_and_closure(api):
    async def handler(channel: Channel, event: Event):
        await channel.send(event, "ready")

    api.bind(handler)
    owner = api.owner()
    channel = await api.connect()

    assert (await api.post(channel)).status_code == 200
    owner.status = PluginStatus.STOPPING
    assert (await api.post(channel)).status_code == 503
    owner.status = PluginStatus.READY
    assert (await api.post(channel)).status_code == 200
    await channel.close()
    assert (await api.post(channel)).status_code == 404


@pytest.mark.anyio
async def test_native_fastapi_routes_are_caller_owned(api):
    from opsmesh.plugins.channels.api import get_app

    async def handler():
        pass

    api.bind(handler)
    channel = await api.connect()
    token = registry._current_plugin.set(api.owners["support"])
    try:
        app = get_app(api.driver)
    finally:
        registry._current_plugin.reset(token)
    assert app is channel.app

    @app.get("/custom")
    async def custom():
        return {"value": "native"}

    route = app.routes[-1]
    try:
        frames = await request(app, method="GET", path="/custom")
        assert json.loads(body_of(frames)) == {"value": "native"}
        await channel.close()
        assert route in app.routes
    finally:
        app.routes.remove(route)
