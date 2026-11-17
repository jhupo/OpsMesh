"""Check the API channel registration, auth, and HTTP invocation contract."""

import json
from types import ModuleType
from typing import Annotated, Any

import anyio
import pytest

from opsmesh.adapters import Channel, Event
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.drivers import base as driver_state
from opsmesh.messages import Message
from opsmesh.params import Context, Depends, EventMessage
from opsmesh.plugins.channels import Message as ChannelMessage
from opsmesh.plugins.channels import MessageSegment, UserContext
from opsmesh.plugins.channels.api import (
    APIChannel,
    APIMessageEvent,
    MessageEntrances,
    Settings,
)
from opsmesh.plugins.manager import PluginManager
from opsmesh.plugins.model import Plugin, PluginStatus
from opsmesh.plugins.registry import _current_plugin
from tests.fixtures.http_identity import caller, denied_caller


def plugin_owner(name: str, *, ready: bool = True) -> Plugin:
    return Plugin(
        name=name,
        module=ModuleType(name),
        module_name=name,
        manager=PluginManager(),
        status=PluginStatus.READY if ready else PluginStatus.LOADED,
    )


def declare_message(router, handler, *, rule=None, identity=caller):
    from opsmesh.matchers import Matcher, MatcherSource

    owner = _current_plugin.get() or plugin_owner("support")
    matcher = Matcher.new(
        type_="message",
        priority=1,
        block=True,
        handlers=[handler],
        rule=rule,
        source=MatcherSource(plugin_id=owner.id_),
    )
    token = _current_plugin.set(owner)
    try:
        registration = router.bind(handlers=(matcher,), identity=identity)
    finally:
        _current_plugin.reset(token)
    return registration


def structured_reply(value):
    return ChannelMessage(
        [
            MessageSegment.text("Result"),
            MessageSegment.structured(value),
        ]
    )


def make_runtime() -> Driver:
    driver = Driver(Env(), Config())
    return driver


def make_settings() -> Settings:
    return Settings()


def scope(
    method: str = "GET",
    path: str = "/",
    *,
    token: str | None = None,
    body: bytes = b"",
    content_type: str = "application/json",
    headers: tuple[tuple[bytes, bytes], ...] = (),
):
    raw = list(headers)
    if token is not None:
        raw.append((b"authorization", f"Bearer {token}".encode()))
    if body:
        raw.append((b"content-type", content_type.encode()))
    messages = [
        {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }
    ]
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": raw,
        "scheme": "http",
        "server": ("testserver", 80),
    }, messages


async def call(server, request):
    request_scope, queue = request
    sent = []

    async def receive():
        if queue:
            return queue.pop(0)
        await anyio.sleep_forever()

    async def send(message):
        sent.append(message)

    await server.app(request_scope, receive, send)
    return sent


def body(messages) -> bytes:
    return b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )


def payload(messages) -> Any:
    return json.loads(body(messages))


async def _setup(monkeypatch, router, settings):
    from opsmesh.plugins import registry

    lookup = registry.get_plugin

    def get_plugin(name):
        return next(
            (
                item.owner
                for item in reversed(router.entries)
                if item.installation_id == name
            ),
            None,
        ) or lookup(name)

    monkeypatch.setattr(registry, "get_plugin", get_plugin)
    driver = make_runtime()
    monkeypatch.setattr(driver_state, "_driver", driver)
    service = APIChannel(settings, router.entries[0])
    await service.startup()
    return service.server, service


def test_transport_settings_reject_business_identity_configuration():
    with pytest.raises(ValueError, match="Extra inputs"):
        Settings(identities=[])


@pytest.mark.anyio
@pytest.mark.parametrize("question", ["/api_probe", "/api_probe hello"])
async def test_api_message_uses_framework_command_and_authenticated_context(
    monkeypatch,
    question,
):
    from opsmesh.params import CommandArg
    from opsmesh.rules import command
    from opsmesh.rules.checks import TrieRule

    monkeypatch.setattr(driver_state, "_driver", make_runtime())

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = MessageEntrances()
    monkeypatch.setattr(TrieRule, "prefix", type(TrieRule.prefix)())

    async def handler(
        channel: "Channel",
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

    registration = declare_message(
        router,
        handler,
        rule=command("api_probe", force_whitespace=True),
    )
    driver, channel = await _setup(monkeypatch, router, make_settings())

    try:
        response = await call(
            driver,
            scope(
                "POST",
                registration.path,
                token="secret",
                body=json.dumps({"message": question}).encode(),
            ),
        )
        assert response[0]["status"] == 200
        assert payload(response)["messages"][0]["data"] == {
            "argument": question.removeprefix("/api_probe").strip(),
            "id": "api:user",
            "roles": ["agent"],
            "groups": ["support"],
        }
    finally:
        await channel.shutdown()
        registration.message_handlers[0].destroy()


@pytest.mark.anyio
async def test_api_channel_can_reply_with_a_framework_message(monkeypatch):
    from opsmesh.adapters import Channel

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = MessageEntrances()

    async def handler(
        channel: Channel,
        event: Event,
        message: Annotated[Message, EventMessage()],
    ):
        await channel.send(event, message)

    registration = declare_message(router, handler)
    driver, channel = await _setup(monkeypatch, router, make_settings())
    try:
        response = await call(
            driver,
            scope(
                "POST",
                registration.path,
                token="secret",
                body=b'{"message":"hello"}',
            ),
        )
        assert response[0]["status"] == 200
        assert payload(response)["messages"] == [
            {
                "message_id": payload(response)["messages"][0]["message_id"],
                "content": [{"type": "text", "text": "hello"}],
                "data": None,
            }
        ]
    finally:
        await channel.shutdown()
        registration.message_handlers[0].destroy()


@pytest.mark.anyio
async def test_authenticated_upload_message_and_download_share_scope(
    monkeypatch,
):
    import httpx

    from opsmesh.plugins import registry

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    monkeypatch.setenv("OTHER_API_TOKEN", "other-secret")
    router = MessageEntrances()

    async def handler(channel: "Channel", event: APIMessageEvent):
        parts = event.get_message()
        assert [part.type for part in parts] == ["text", "file"]
        data = await event.attachments.read(parts[1].data["attachment_id"])
        await channel.send(
            event,
            structured_reply(
                {"text": parts.extract_plain_text(), "file": data.decode()}
            ),
        )

    registration = declare_message(router, handler)
    monkeypatch.setattr(
        registry,
        "get_plugin",
        lambda name: registration.owner if name == "support" else None,
    )
    settings = make_settings()
    _, channel = await _setup(monkeypatch, router, settings)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=channel.app),
            base_url="http://test",
        ) as client:
            result = await client.post(
                "/attachments/support?name=notes.txt",
                content=b"hello",
                headers={"content-type": "text/plain"},
            )
            assert result.status_code == 401
            headers = {
                "authorization": "Bearer secret",
                "content-type": "text/plain",
            }
            result = await client.post(
                "/attachments/support?name=notes.txt",
                content=b"hello",
                headers=headers,
            )
            assert result.status_code == 201
            attachment_id = result.json()["id"]
            path = f"/attachments/support/{attachment_id}"
            assert (await client.get(path, headers=headers)).content == b"hello"
            other = {"authorization": "Bearer other-secret"}
            assert (await client.get(path, headers=other)).status_code == 404
            body = {
                "message": [
                    {"type": "text", "text": "read"},
                    {"type": "file", "attachment_id": attachment_id},
                ]
            }
            result = await client.post(
                "/plugins/support/messages",
                json=body,
                headers={"authorization": "Bearer secret"},
            )
            assert result.json()["messages"][0]["data"] == {
                "text": "read",
                "file": "hello",
            }
            assert (
                await client.post(
                    "/plugins/support/messages", json=body, headers=other
                )
            ).status_code == 404
            registration.owner.status = PluginStatus.LOADED
            assert (await client.get(path, headers=headers)).status_code == 503
    finally:
        await channel.shutdown()
    assert not channel.uploads._uploads


@pytest.mark.anyio
@pytest.mark.parametrize(
    "request_body",
    [
        {},
        {"message": ""},
        {"message": "  "},
        {"message": []},
        {"message": {"text": "hello"}},
    ],
)
async def test_message_binding_rejects_missing_or_invalid_message(
    monkeypatch, request_body
):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = MessageEntrances()

    async def handler():
        raise AssertionError("Invalid messages must not reach a matcher")

    registration = declare_message(router, handler)
    server, channel = await _setup(monkeypatch, router, make_settings())
    try:
        result = await call(
            server,
            scope(
                "POST",
                registration.path,
                token="secret",
                body=json.dumps(request_body).encode(),
            ),
        )
        assert result[0]["status"] == 422
    finally:
        registration.message_handlers[0].destroy()
        await channel.shutdown()


@pytest.mark.anyio
async def test_message_handler_return_is_not_an_implicit_reply(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = MessageEntrances()

    async def handler():
        return {"ignored": True}

    registration = declare_message(router, handler)
    server, channel = await _setup(monkeypatch, router, make_settings())
    try:
        result = await call(
            server,
            scope(
                "POST",
                registration.path,
                token="secret",
                body=b'{"message":"hello"}',
            ),
        )
        assert result[0]["status"] == 200
        assert payload(result)["messages"] == []
        assert payload(result)["status"] == "completed"
    finally:
        registration.message_handlers[0].destroy()
        await channel.shutdown()


@pytest.mark.anyio
async def test_message_handler_sends_multiple_complete_messages(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = MessageEntrances()

    async def handler(channel: Channel, event: Event):
        await channel.send(event, "first")
        await channel.send(event, "second")

    registration = declare_message(router, handler)
    server, channel = await _setup(monkeypatch, router, make_settings())
    try:
        result = await call(
            server,
            scope(
                "POST",
                registration.path,
                token="secret",
                body=b'{"message":"hello"}',
            ),
        )
        assert result[0]["status"] == 200
        assert [
            item["content"][0]["text"] for item in payload(result)["messages"]
        ] == [
            "first",
            "second",
        ]
    finally:
        registration.message_handlers[0].destroy()
        await channel.shutdown()


@pytest.mark.anyio
async def test_image_only_api_request_stays_a_framework_message(monkeypatch):
    from opsmesh.plugins.channels import Attachment

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = MessageEntrances()

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

    registration = declare_message(router, handler)
    server, channel = await _setup(monkeypatch, router, make_settings())
    image = channel.uploads.put(
        ("support", "api:user"),
        Attachment.create("image.png", "image/png"),
        b"image-content",
    )
    try:
        result = await call(
            server,
            scope(
                "POST",
                registration.path,
                token="secret",
                body=json.dumps(
                    {"message": [{"type": "image", "attachment_id": image.id}]}
                ).encode(),
            ),
        )
        assert payload(result)["messages"][0]["data"] == {
            "type": "message",
            "parts": ["image"],
        }
    finally:
        registration.message_handlers[0].destroy()
        await channel.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("spec", ["2.0", "2.4"])
async def test_message_transport_retains_owner_after_handler_cleanup(
    monkeypatch, streamed, spec
):
    from opsmesh.plugins.channels import Delivery
    from opsmesh.plugins.channels.api.events import APIMessageEvent
    from opsmesh.plugins.invocation import _current_call
    from opsmesh.plugins.registry import get_current_plugin

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = MessageEntrances()
    closed, observed = [], []
    cleaned = anyio.Event()
    owner = plugin_owner("support")

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

    token = _current_plugin.set(owner)
    try:
        declare_message(router, handler)
    finally:
        _current_plugin.reset(token)
    server, channel = await _setup(monkeypatch, router, make_settings())
    request_scope, queued = scope(
        "POST",
        "/plugins/support/messages",
        token="secret",
        body=json.dumps({"message": "hello"}).encode(),
        headers=((b"accept", b"text/event-stream"),) if streamed else (),
    )
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

    try:
        with anyio.fail_after(5):
            await server.app(request_scope, receive, send)
        assert observed == ["written"]
        assert not owner.calls
        assert not channel._active_conversations
    finally:
        await channel.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("streamed", [False, True])
async def test_message_execution_timeout_applies_to_json_and_sse(
    monkeypatch, streamed
):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = MessageEntrances()
    closed = []

    async def handler():
        try:
            await anyio.sleep_forever()
        finally:
            closed.append(True)

    registration = declare_message(router, handler)
    settings = make_settings().model_copy(
        update={"request_timeout_seconds": 0.02}
    )
    server, channel = await _setup(monkeypatch, router, settings)
    try:
        with anyio.fail_after(2):
            result = await call(
                server,
                scope(
                    "POST",
                    registration.path,
                    token="secret",
                    body=b'{"message":"hello"}',
                    headers=((b"accept", b"text/event-stream"),)
                    if streamed
                    else (),
                ),
            )
        assert closed == [True]
        assert b"request timed out" in body(result)
        if not streamed:
            assert result[0]["status"] == 504
        assert not channel._active_conversations
    finally:
        registration.message_handlers[0].destroy()
        await channel.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path", ["/plugins/support/messages", "/attachments/support"]
)
async def test_slow_body_releases_plugin_admission(monkeypatch, path):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    entrances = MessageEntrances()

    async def handler():
        pytest.fail("An incomplete body must not reach the handler")

    entrance = declare_message(entrances, handler)
    settings = make_settings().model_copy(
        update={"request_timeout_seconds": 0.02}
    )
    server, channel = await _setup(monkeypatch, entrances, settings)
    request, _ = scope("POST", path, token="secret")
    frames = []

    async def receive():
        await anyio.sleep_forever()

    async def send(frame):
        frames.append(frame)

    try:
        with anyio.fail_after(2):
            await server.app(request, receive, send)
        assert frames[0]["status"] == 504
        assert not entrance.owner.calls
    finally:
        entrance.message_handlers[0].destroy()
        await channel.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("streamed", [False, True])
async def test_body_and_handler_share_one_deadline(monkeypatch, streamed):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    entrances = MessageEntrances()

    async def handler(channel: Channel, event: Event):
        await anyio.sleep(0.25)
        await channel.send(event, "too late")

    entrance = declare_message(entrances, handler)
    settings = make_settings().model_copy(
        update={"request_timeout_seconds": 0.4}
    )
    server, channel = await _setup(monkeypatch, entrances, settings)
    request, queued = scope(
        "POST",
        entrance.path,
        token="secret",
        body=b'{"message":"hello"}',
        headers=((b"accept", b"text/event-stream"),) if streamed else (),
    )
    frames = []

    async def receive():
        if queued:
            await anyio.sleep(0.25)
            return queued.pop(0)
        await anyio.sleep_forever()

    async def send(frame):
        frames.append(frame)

    try:
        with anyio.fail_after(2):
            await server.app(request, receive, send)
        assert b"request timed out" in body(frames)
        assert b"too late" not in body(frames)
        assert not entrance.owner.calls
        assert not channel._active_conversations
    finally:
        entrance.message_handlers[0].destroy()
        await channel.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("streamed", [False, True])
async def test_permission_error_is_safe_in_json_and_sse(monkeypatch, streamed):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    entrances = MessageEntrances()

    async def handler():
        raise PermissionError("private resource and credential details")

    entrance = declare_message(entrances, handler)
    server, channel = await _setup(monkeypatch, entrances, make_settings())
    try:
        frames = await call(
            server,
            scope(
                "POST",
                entrance.path,
                token="secret",
                body=b'{"message":"hello"}',
                headers=((b"accept", b"text/event-stream"),)
                if streamed
                else (),
            ),
        )
        assert b"request is not permitted" in body(frames)
        assert b"private resource" not in body(frames)
        assert b"403" in body(frames)
        assert not entrance.owner.calls
    finally:
        entrance.message_handlers[0].destroy()
        await channel.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "token,status", [(None, 401), ("wrong", 401), ("secret", 403)]
)
async def test_authentication_precedes_message_dispatch(
    monkeypatch, token, status
):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    entrances = MessageEntrances()

    async def handler():
        pytest.fail("Unauthorized request reached a matcher")

    entrance = declare_message(entrances, handler, identity=denied_caller)
    settings = make_settings()
    server, channel = await _setup(monkeypatch, entrances, settings)
    try:
        result = await call(
            server,
            scope(
                "POST", entrance.path, token=token, body=b'{"message":"hello"}'
            ),
        )
        assert result[0]["status"] == status
        assert not entrance.owner.calls
    finally:
        entrance.message_handlers[0].destroy()
        await channel.shutdown()


@pytest.mark.anyio
async def test_fixed_endpoint_observes_plugin_state_and_revocation(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    entrances = MessageEntrances()

    async def handler(channel: Channel, event: Event):
        await channel.send(event, "ready")

    entrance = declare_message(entrances, handler)
    server, channel = await _setup(monkeypatch, entrances, make_settings())

    async def request():
        return await call(
            server,
            scope(
                "POST",
                entrance.path,
                token="secret",
                body=b'{"message":"hello"}',
            ),
        )

    try:
        assert (await request())[0]["status"] == 200
        entrance.owner.status = PluginStatus.STOPPING
        assert (await request())[0]["status"] == 503
        entrance.owner.status = PluginStatus.READY
        assert (await request())[0]["status"] == 200
        await channel.shutdown()
        assert (await request())[0]["status"] == 404
    finally:
        entrance.message_handlers[0].destroy()
        await channel.shutdown()


@pytest.mark.anyio
async def test_native_fastapi_routes_are_caller_owned(monkeypatch):
    from opsmesh.plugins.channels.api import get_app

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    entrances = MessageEntrances()

    async def handler():
        pass

    entrance = declare_message(entrances, handler)
    server, channel = await _setup(monkeypatch, entrances, make_settings())
    token = _current_plugin.set(entrance.owner)
    try:
        app = get_app()
    finally:
        _current_plugin.reset(token)

    @app.get("/custom")
    async def custom():
        return {"value": "native"}

    route = app.routes[-1]
    try:
        result = await call(server, scope("GET", "/custom"))
        assert payload(result) == {"value": "native"}
        await channel.shutdown()
        assert route in app.routes
    finally:
        app.routes.remove(route)


def test_message_binding_rejects_foreign_and_ambiguous_matchers():
    from opsmesh.matchers import Matcher, MatcherSource
    from opsmesh.plugins.channels.api import EntranceError

    owner = plugin_owner("support")
    foreign = Matcher.new(block=True, source=MatcherSource(plugin_id="other"))
    first = Matcher.new(block=True, source=MatcherSource(plugin_id=owner.id_))
    second = Matcher.new(block=True, source=MatcherSource(plugin_id=owner.id_))
    token = _current_plugin.set(owner)
    try:
        entrances = MessageEntrances()
        with pytest.raises(EntranceError, match="belong"):
            entrances.bind(handlers=(foreign,), identity=caller)
        with pytest.raises(EntranceError, match="distinct priorities"):
            entrances.bind(handlers=(first, second), identity=caller)
        assert not entrances.entries
    finally:
        _current_plugin.reset(token)
        for matcher in (foreign, first, second):
            matcher.destroy()


@pytest.mark.anyio
async def test_consumer_identity_dependency_owns_all_http_entrypoints(
    monkeypatch,
):
    import httpx
    from pydantic import InstanceOf
    from starlette.requests import Request

    from opsmesh.plugins import get_current_plugin

    observed = []
    closed = []

    async def resource():
        owner = get_current_plugin()
        assert owner.id_ == "support"
        assert owner.calls
        try:
            yield owner.id_
        finally:
            closed.append(owner.id_)

    async def identity(
        request: Annotated[InstanceOf[Request], Context()],
        owner: Annotated[str, Depends(resource)],
    ) -> UserContext:
        observed.append((request.url.path, owner))
        return UserContext("explicit-anonymous")

    async def handler(user: Annotated[UserContext, Context()]):
        assert user.id == "explicit-anonymous"

    entrances = MessageEntrances()
    entrance = declare_message(entrances, handler, identity=identity)
    _, channel = await _setup(monkeypatch, entrances, make_settings())
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=channel.app),
            base_url="http://test",
        ) as client:
            upload = await client.post(
                "/attachments/support?name=a.txt", content=b"data"
            )
            assert upload.status_code == 201
            download = await client.get(
                "/attachments/support/" + upload.json()["id"]
            )
            assert download.content == b"data"
            message = await client.post(
                entrance.path, json={"message": "hello"}
            )
            assert message.status_code == 200
        assert len(observed) == len(closed) == 3
        assert not entrance.owner.calls
    finally:
        entrance.message_handlers[0].destroy()
        await channel.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/plugins/support/messages"),
        ("POST", "/attachments/support"),
        ("GET", "/attachments/support/unknown"),
    ],
)
async def test_identity_timeout_closes_consumer_dependencies(
    monkeypatch, method, path
):
    closed = []

    async def resource():
        try:
            yield "resource"
        finally:
            closed.append(True)

    async def identity(value: Annotated[str, Depends(resource)]) -> UserContext:
        await anyio.sleep_forever()

    async def handler():
        pytest.fail("Identity timeout must precede dispatch")

    entrances = MessageEntrances()
    entrance = declare_message(entrances, handler, identity=identity)
    server, channel = await _setup(
        monkeypatch, entrances, Settings(request_timeout_seconds=0.02)
    )
    try:
        result = await call(
            server, scope(method, path, body=b'{"message":"hello"}')
        )
        assert result[0]["status"] == 504
        assert closed == [True]
        assert not entrance.owner.calls
    finally:
        entrance.message_handlers[0].destroy()
        await channel.shutdown()
