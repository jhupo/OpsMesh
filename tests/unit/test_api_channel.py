"""Check the API channel registration, auth, and HTTP invocation contract."""

import json
from types import ModuleType
from typing import Annotated, Any

import anyio
import pytest
from pydantic import BaseModel
from starlette.requests import ClientDisconnect
from starlette.responses import (
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)

import opsmesh.plugins.channels.api as api_module
from opsmesh.adapters import Channel, Event
from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.drivers import base as driver_state
from opsmesh.messages import Message
from opsmesh.params import Context, Depends, EventMessage
from opsmesh.plugins.channels import Message as ChannelMessage
from opsmesh.plugins.channels import MessageSegment, UserContext
from opsmesh.plugins.channels.api import (
    ACCESS_ADMIN,
    APIChannel,
    APIConfigurationError,
    APIRouter,
    HTTPError,
    HTTPEvent,
    IdentitySettings,
    RouteConflict,
    RouteRegistrationError,
    Settings,
    json_response,
    sse_response,
)
from opsmesh.plugins.channels.api.routes import normalize_relative_path
from opsmesh.plugins.channels.api.streaming import (
    ManagedStreamingResponse,
    StreamLease,
)
from opsmesh.plugins.model import Plugin, PluginStatus
from opsmesh.plugins.registry import _current_plugin


def plugin_owner(name: str, *, ready: bool = True) -> Plugin:
    return Plugin(
        name=name,
        module=ModuleType(name),
        module_name=name,
        manager=None,
        status=PluginStatus.READY if ready else PluginStatus.LOADED,
    )


def declare_message(router, handler, *, rule=None):
    from opsmesh.matchers import Matcher, MatcherSource
    from opsmesh.plugins.channels.api import MessageRequest

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
        registration = router.register(
            method="POST",
            path="/messages",
            body=MessageRequest,
            message_handlers=(matcher,),
            allow_response=True,
            response_types=(Response,),
        )
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


@pytest.mark.anyio
async def test_fastapi_disconnect_cancels_framework_handler(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()
    entered, closed = anyio.Event(), anyio.Event()

    async def handler():
        entered.set()
        try:
            await anyio.sleep_forever()
        finally:
            closed.set()

    declare_route(router, method="GET", path="/slow", handler=handler)
    server, channel = await _setup(monkeypatch, router, make_settings())
    request_scope, messages = scope(
        path="/plugins/support/slow", token="secret"
    )

    async def receive():
        if messages:
            return messages.pop(0)
        await entered.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    try:
        with anyio.fail_after(2), pytest.raises(ClientDisconnect):
            await server.app(request_scope, receive, send)
        assert closed.is_set()
    finally:
        await channel.shutdown()


@pytest.mark.anyio
async def test_fastapi_rejects_oversized_body_before_dispatch(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def handler():
        raise AssertionError("Oversized bodies must not reach the framework")

    declare_route(router, method="POST", path="/body", handler=handler)
    settings = make_settings().model_copy(update={"max_request_body": 2})
    server, channel = await _setup(monkeypatch, router, settings)
    try:
        result = await call(
            server,
            scope("POST", "/plugins/support/body", token="secret", body=b"123"),
        )
        assert result[0]["status"] == 413
    finally:
        await channel.shutdown()


def declare_route(
    router,
    *,
    method,
    path,
    handler,
    plugin_id="support",
    result_type=None,
    ready=True,
    **options,
):
    """Declare test routes through the public decorator and plugin scope."""
    owner = _current_plugin.get()
    if owner is None:
        owner = Plugin(
            name=plugin_id,
            module=ModuleType(plugin_id),
            module_name=plugin_id,
            manager=None,
            status=PluginStatus.READY if ready else PluginStatus.LOADED,
        )
    handler.__annotations__["return"] = result_type or (Response | Any)
    previous = api_module.routing._router
    api_module.routing._router = router
    token = _current_plugin.set(owner)
    try:
        api_module.route(method, path, **options)(handler)
    finally:
        _current_plugin.reset(token)
        api_module.routing._router = previous
    return router.registrations[-1]


def make_runtime() -> Driver:
    driver = Driver(Env(), Config())
    return driver


def make_settings(*identities: IdentitySettings) -> Settings:
    return Settings(
        identities=identities
        or (IdentitySettings(id="user", token_ref="TEST_API_TOKEN"),)
    )


def identity(
    id: str = "user",
    *,
    token_ref: str = "TEST_API_TOKEN",
    groups: tuple[str, ...] = ("support",),
    roles: tuple[str, ...] = ("agent",),
    allow_invoke: bool = True,
    allow_admin: bool = False,
    name: str | None = None,
) -> IdentitySettings:
    return IdentitySettings(
        id=id,
        token_ref=token_ref,
        groups=groups,
        roles=roles,
        allow_invoke=allow_invoke,
        allow_admin=allow_admin,
        name=name,
    )


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


class EchoBody(BaseModel):
    text: str


class EchoResult(BaseModel):
    text: str
    caller: str


async def _setup(monkeypatch, router, settings):
    from opsmesh.plugins import registry

    lookup = registry.get_plugin

    def get_plugin(name):
        return next(
            (
                item.owner
                for item in reversed(router.registrations)
                if item.installation_id == name
            ),
            None,
        ) or lookup(name)

    monkeypatch.setattr(registry, "get_plugin", get_plugin)
    driver = make_runtime()
    monkeypatch.setattr(driver_state, "_driver", driver)
    service = APIChannel(settings, router)
    await service.startup()
    return service.server, service


@pytest.mark.anyio
async def test_route_invocation_passes_caller_and_path_params(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def handler(
        event: HTTPEvent,
        caller: Annotated[UserContext, Context()],
    ):
        assert event.request.method == "GET"
        assert isinstance(caller, UserContext)
        return json_response(
            {
                "id": caller.id,
                "name": caller.name,
                "groups": sorted(caller.groups),
                "roles": sorted(caller.roles),
                "session": event.request.path_params["session_id"],
            }
        )

    registration = declare_route(
        router,
        method="GET",
        path="/agents/{agent_key}/sessions/{session_id}",
        ready=False,
        handler=handler,
        plugin_id="support",
    )
    driver, service = await _setup(
        monkeypatch, router, make_settings(identity(name="Support User"))
    )
    try:
        path = "/plugins/support/agents/support/sessions/s-1"
        not_ready = await call(driver, scope(path=path, token="secret"))
        assert not_ready[0]["status"] == 503

        registration.owner.status = PluginStatus.READY
        messages = await call(driver, scope(path=path, token="secret"))
        assert messages[0]["status"] == 200
        assert payload(messages) == {
            "id": "api:user",
            "name": "Support User",
            "groups": ["support"],
            "roles": ["agent"],
            "session": "s-1",
        }
    finally:
        await service.shutdown()


@pytest.mark.anyio
async def test_authentication_and_separate_admin_access(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "invoke-token")
    monkeypatch.setenv("TEST_API_ADMIN", "admin-token")
    router = APIRouter()

    async def invoke_handler():
        return {"ok": True}

    async def admin_handler():
        return {"admin": True}

    declare_route(
        router,
        method="GET",
        path="/invoke",
        handler=invoke_handler,
        plugin_id="support",
    )
    declare_route(
        router,
        method="GET",
        path="/admin",
        handler=admin_handler,
        access=ACCESS_ADMIN,
        plugin_id="support",
    )
    settings = make_settings(
        identity("invoker", token_ref="TEST_API_TOKEN"),
        identity(
            "admin",
            token_ref="TEST_API_ADMIN",
            allow_invoke=False,
            allow_admin=True,
        ),
    )
    driver, service = await _setup(monkeypatch, router, settings)
    try:
        missing = await call(driver, scope(path="/plugins/support/invoke"))
        assert missing[0]["status"] == 401
        assert (b"www-authenticate", b"Bearer") in missing[0]["headers"]

        wrong = await call(
            driver,
            scope(path="/plugins/support/invoke", token="unknown"),
        )
        assert wrong[0]["status"] == 401

        forbidden = await call(
            driver,
            scope(path="/plugins/support/admin", token="invoke-token"),
        )
        assert forbidden[0]["status"] == 403

        allowed = await call(
            driver,
            scope(path="/plugins/support/admin", token="admin-token"),
        )
        assert allowed[0]["status"] == 200
        assert payload(allowed) == {"admin": True}

        invoke_forbidden = await call(
            driver,
            scope(path="/plugins/support/invoke", token="admin-token"),
        )
        assert invoke_forbidden[0]["status"] == 403
    finally:
        await service.shutdown()


@pytest.mark.anyio
async def test_body_and_response_contracts_are_encoded(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def handler(
        event: HTTPEvent,
        caller: Annotated[UserContext, Context()],
    ) -> EchoResult:
        return EchoResult(text=event.payload.text, caller=caller.id)

    declare_route(
        router,
        method="POST",
        path="/echo",
        handler=handler,
        body=EchoBody,
        result_type=EchoResult,
        plugin_id="support",
    )
    driver, service = await _setup(
        monkeypatch, router, make_settings(identity())
    )
    try:
        path = "/plugins/support/echo"
        invalid = await call(
            driver,
            scope(
                "POST",
                path,
                token="secret",
                body=b"{",
            ),
        )
        assert invalid[0]["status"] == 400

        mismatch = await call(
            driver,
            scope(
                "POST",
                path,
                token="secret",
                body=json.dumps({"other": "value"}).encode(),
            ),
        )
        assert mismatch[0]["status"] == 422

        wrong_type = await call(
            driver,
            scope(
                "POST",
                path,
                token="secret",
                body=json.dumps({"text": "hello"}).encode(),
                content_type="text/plain",
            ),
        )
        assert wrong_type[0]["status"] == 415

        valid = await call(
            driver,
            scope(
                "POST",
                path,
                token="secret",
                body=json.dumps({"text": "hello"}).encode(),
            ),
        )
        assert valid[0]["status"] == 200
        assert payload(valid) == {"text": "hello", "caller": "api:user"}
    finally:
        await service.shutdown()


@pytest.mark.anyio
async def test_unknown_and_unsupported_routes_use_http_semantics(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def handler():
        return {"ok": True}

    declare_route(
        router,
        method="GET",
        path="/known",
        handler=handler,
        plugin_id="support",
    )
    driver, service = await _setup(
        monkeypatch, router, make_settings(identity())
    )
    try:
        missing = await call(
            driver, scope(path="/plugins/support/missing", token="secret")
        )
        assert missing[0]["status"] == 404

        mismatch = await call(
            driver,
            scope("POST", "/plugins/support/known", token="secret"),
        )
        assert mismatch[0]["status"] == 405
        assert set(dict(mismatch[0]["headers"])[b"allow"].split(b", ")) == {
            b"GET",
            b"HEAD",
        }
    finally:
        await service.shutdown()


@pytest.mark.anyio
async def test_health_and_readiness_are_generic_channels(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()
    driver, service = await _setup(
        monkeypatch, router, make_settings(identity())
    )
    try:
        health = await call(driver, scope(path="/healthz"))
        ready = await call(driver, scope(path="/readyz"))
        assert health[0]["status"] == 200
        assert payload(health) == {"status": "ok"}
        assert ready[0]["status"] == 200
        assert payload(ready)["status"] == "ready"
    finally:
        await service.shutdown()


def test_route_conflicts_keep_the_existing_registration():
    router = APIRouter()

    async def handler():
        return None

    first = declare_route(
        router,
        method="GET",
        path="/agents/{agent_key}",
        handler=handler,
        plugin_id="support",
    )
    with pytest.raises(RouteConflict, match="already registered"):
        declare_route(
            router,
            method="GET",
            path="/agents/{agent_key}",
            handler=handler,
            plugin_id="support",
        )
    with pytest.raises(RouteConflict, match="ambiguous"):
        declare_route(
            router,
            method="GET",
            path="/agents/{other_key}",
            handler=handler,
            plugin_id="support",
        )
    assert router.registrations == (first,)


def test_route_paths_are_normalized_and_validated():
    assert normalize_relative_path("/a//b/") == "/a/b"
    assert normalize_relative_path("/a/./b") == "/a/b"
    with pytest.raises(RouteRegistrationError):
        normalize_relative_path("relative")
    with pytest.raises(RouteRegistrationError):
        normalize_relative_path("/a/../b")
    with pytest.raises(RouteRegistrationError):
        normalize_relative_path("/a/{bad-name}")
    with pytest.raises(RouteRegistrationError):
        normalize_relative_path("/")
    with pytest.raises(RouteRegistrationError):
        normalize_relative_path("/a?query=1")


def test_route_registration_requires_an_owner_and_known_method():
    router = APIRouter()

    async def handler():
        return None

    with pytest.raises(RouteRegistrationError, match="loading"):
        router.register(method="GET", path="/x", handler=handler)
    with pytest.raises(RouteRegistrationError, match="Unsupported"):
        declare_route(
            router,
            method="HEAD",
            path="/x",
            handler=handler,
            plugin_id="support",
        )
    with pytest.raises(RouteRegistrationError, match="installation id"):
        declare_route(
            router,
            method="GET",
            path="/x",
            handler=handler,
            plugin_id="bad/id",
        )


@pytest.mark.anyio
async def test_late_registration_mounts_and_stays_unready(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()
    driver, service = await _setup(
        monkeypatch, router, make_settings(identity())
    )
    try:

        async def handler():
            return {"late": True}

        registration = declare_route(
            router,
            method="GET",
            path="/late",
            ready=False,
            handler=handler,
            plugin_id="support",
        )
        path = "/plugins/support/late"
        unready = await call(driver, scope(path=path, token="secret"))
        assert unready[0]["status"] == 503

        registration.owner.status = PluginStatus.READY
        ready = await call(driver, scope(path=path, token="secret"))
        assert ready[0]["status"] == 200
        assert payload(ready) == {"late": True}
    finally:
        await service.shutdown()


@pytest.mark.anyio
async def test_stream_dependencies_release_after_the_body(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()
    events: list[str] = []

    async def dependency():
        events.append("enter")
        try:
            yield "value"
        finally:
            events.append("release")

    async def frames():
        events.append("first")
        yield b"data: first\n\n"
        events.append("second")
        yield b"data: second\n\n"

    def handler(value: str = Depends(dependency)):
        assert value == "value"
        return sse_response(frames())

    declare_route(
        router,
        method="GET",
        path="/stream",
        handler=handler,
        plugin_id="support",
    )
    driver, service = await _setup(
        monkeypatch, router, make_settings(identity())
    )
    try:
        messages = await call(
            driver,
            scope(path="/plugins/support/stream", token="secret"),
        )
        assert messages[0]["status"] == 200
        assert body(messages) == b"data: first\n\ndata: second\n\n"
        assert events == ["enter", "first", "second", "release"]
    finally:
        await service.shutdown()


@pytest.mark.anyio
async def test_missing_or_duplicate_tokens_fail_startup(monkeypatch):
    monkeypatch.delenv("TEST_API_TOKEN", raising=False)
    driver = make_runtime()
    monkeypatch.setattr(driver_state, "_driver", driver)
    service = APIChannel(
        make_settings(identity()),
        APIRouter(),
    )
    with pytest.raises(APIConfigurationError, match="TEST_API_TOKEN"):
        await service.startup()

    monkeypatch.setenv("TEST_API_TOKEN", "same")
    monkeypatch.setenv("TEST_API_TOKEN_2", "same")
    service = APIChannel(
        make_settings(
            identity("one", token_ref="TEST_API_TOKEN"),
            identity("two", token_ref="TEST_API_TOKEN_2"),
        ),
        APIRouter(),
    )
    with pytest.raises(APIConfigurationError, match="distinct"):
        await service.startup()


def test_settings_require_identities_and_distinct_namespaces():
    with pytest.raises(ValueError):
        Settings(identities=())
    with pytest.raises(ValueError, match="Duplicate"):
        Settings(
            identities=(
                identity("same", token_ref="ONE"),
                identity("same", token_ref="TWO"),
            )
        )
    with pytest.raises(ValueError, match="distinct token_ref"):
        Settings(
            identities=(
                identity("one", token_ref="SAME"),
                identity("two", token_ref="SAME"),
            )
        )


@pytest.mark.anyio
async def test_declarative_route_derives_contracts_and_waits_for_owner(
    monkeypatch,
):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()
    monkeypatch.setattr(api_module.routing, "_router", router)
    owner = plugin_owner("support", ready=False)
    token = _current_plugin.set(owner)
    try:

        @api_module.post("/echo", body=EchoBody)
        async def handler(
            event: HTTPEvent,
            caller: Annotated[UserContext, Context()],
        ) -> EchoResult:
            return EchoResult(
                text=event.payload.text,
                caller=caller.id,
            )

    finally:
        _current_plugin.reset(token)

    registration = router.registrations[0]
    assert registration.body_model is EchoBody
    assert registration.response_adapter is not None
    driver, service = await _setup(
        monkeypatch, router, make_settings(identity())
    )
    try:
        path = "/plugins/support/echo"
        request = scope("POST", path, token="secret", body=b'{"text":"hi"}')
        not_ready = await call(driver, request)
        assert not_ready[0]["status"] == 503

        owner.status = PluginStatus.READY
        ready = await call(
            driver,
            scope("POST", path, token="secret", body=b'{"text":"hi"}'),
        )
        assert ready[0]["status"] == 200
        assert payload(ready) == {"text": "hi", "caller": "api:user"}
    finally:
        await service.shutdown()


def test_declarative_contract_declarations_are_validated(monkeypatch):
    router = APIRouter()
    monkeypatch.setattr(api_module.routing, "_router", router)
    owner = plugin_owner("support")
    token = _current_plugin.set(owner)
    try:
        with pytest.raises(RouteRegistrationError, match="return type"):

            @api_module.post("/missing-return")
            async def missing(event: Event):
                return {"ok": True}

        with pytest.raises(RouteRegistrationError, match="body"):

            @api_module.post("/invalid-schema", body=str)
            async def invalid(event: Event) -> dict:
                return {"ok": True}

    finally:
        _current_plugin.reset(token)


@pytest.mark.anyio
async def test_failure_reaches_generator_dependencies(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()
    events: list[str] = []

    async def dependency():
        try:
            yield "value"
        except Exception:
            events.append("rollback")
            raise

    async def handler(value: str = Depends(dependency)):
        raise HTTPError(400, "invalid")

    declare_route(
        router,
        method="GET",
        path="/rollback",
        handler=handler,
        plugin_id="support",
    )
    driver, service = await _setup(
        monkeypatch, router, make_settings(identity())
    )
    try:
        messages = await call(
            driver,
            scope(path="/plugins/support/rollback", token="secret"),
        )
        assert messages[0]["status"] == 400
        assert events == ["rollback"]
    finally:
        await service.shutdown()


@pytest.mark.anyio
async def test_dependency_commit_failure_is_a_server_error(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def dependency():
        yield "value"
        raise RuntimeError("commit failed")

    async def handler(value: str = Depends(dependency)):
        return {"ok": True}

    declare_route(
        router,
        method="GET",
        path="/commit",
        handler=handler,
        plugin_id="support",
    )
    driver, service = await _setup(
        monkeypatch, router, make_settings(identity())
    )
    try:
        messages = await call(
            driver,
            scope(path="/plugins/support/commit", token="secret"),
        )
        assert messages[0]["status"] == 500
        assert payload(messages) == {"error": "internal server error"}
    finally:
        await service.shutdown()


@pytest.mark.anyio
async def test_non_ascii_and_repeated_authorization_headers_are_rejected(
    monkeypatch,
):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def handler():
        return {"ok": True}

    declare_route(
        router,
        method="GET",
        path="/auth",
        handler=handler,
        plugin_id="support",
    )
    driver, service = await _setup(
        monkeypatch, router, make_settings(identity())
    )
    try:
        path = "/plugins/support/auth"
        non_ascii = await call(driver, scope(path=path, token="\u5bc6\u94a5"))
        assert non_ascii[0]["status"] == 401

        repeated = await call(
            driver,
            scope(
                path=path,
                headers=(
                    (b"authorization", b"Bearer secret"),
                    (b"authorization", b"Bearer secret"),
                ),
            ),
        )
        assert repeated[0]["status"] == 401
    finally:
        await service.shutdown()


@pytest.mark.anyio
async def test_configured_stream_error_message_reaches_the_client(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def failing():
        yield b"data: first\n\n"
        raise RuntimeError("private detail")

    async def handler():
        return sse_response(failing())

    declare_route(
        router,
        method="GET",
        path="/broken-stream",
        handler=handler,
        plugin_id="support",
    )
    settings = Settings(
        identities=(identity(),), stream_error_message="safe failure"
    )
    driver, service = await _setup(monkeypatch, router, settings)
    try:
        messages = await call(
            driver,
            scope(path="/plugins/support/broken-stream", token="secret"),
        )
        output = body(messages)
        assert b"event: error" in output
        assert b'"safe failure"' in output
        assert b"private detail" not in output
    finally:
        await service.shutdown()


@pytest.mark.anyio
async def test_shutdown_removes_mounted_routes(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def handler():
        return {"ok": True}

    declare_route(
        router,
        method="GET",
        path="/gone",
        handler=handler,
        plugin_id="support",
    )
    driver, service = await _setup(
        monkeypatch, router, make_settings(identity())
    )
    await service.shutdown()
    messages = await call(
        driver, scope(path="/plugins/support/gone", token="secret")
    )
    assert messages[0]["status"] == 404
    health = await call(driver, scope(path="/healthz"))
    assert health[0]["status"] == 404


@pytest.mark.anyio
async def test_decorator_resolves_postponed_annotations(monkeypatch):
    import sys
    from types import ModuleType

    module = ModuleType("postponed_route_probe")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(
        "from __future__ import annotations\n"
        "from typing import Annotated\n"
        "from pydantic import BaseModel\n"
        "from opsmesh.adapters import Event\n"
        "class Body(BaseModel):\n    text: str\n"
        "class Reply(BaseModel):\n    text: str\n"
        "async def handler(event: Event) -> Reply:\n"
        "    return Reply(text=event.payload.text)\n",
        module.__dict__,
    )
    router = APIRouter()
    monkeypatch.setattr(api_module.routing, "_router", router)
    owner = plugin_owner("postponed")
    token = _current_plugin.set(owner)
    try:
        api_module.post("/echo", body=module.Body)(module.handler)
    finally:
        _current_plugin.reset(token)
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    driver, channel = await _setup(monkeypatch, router, make_settings())
    try:
        messages = await call(
            driver,
            scope(
                "POST",
                "/plugins/postponed/echo",
                token="secret",
                body=b'{"text":"hello"}',
            ),
        )
        assert (messages[0]["status"], payload(messages)) == (
            200,
            {"text": "hello"},
        )
    finally:
        await channel.shutdown()


@pytest.mark.anyio
async def test_native_response_cannot_bypass_declared_model(monkeypatch):
    from starlette.responses import Response

    router = APIRouter()

    async def handler():
        return Response(status_code=200, content="unvalidated")

    declare_route(
        router,
        method="GET",
        path="/reply",
        handler=handler,
        result_type=EchoResult,
        plugin_id="strict",
    )
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    driver, channel = await _setup(monkeypatch, router, make_settings())
    try:
        messages = await call(
            driver,
            scope(
                "GET",
                "/plugins/strict/reply",
                token="secret",
            ),
        )
        assert messages[0]["status"] == 500
    finally:
        await channel.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("fail_on_body", [False, True])
async def test_stream_transport_failure_reaches_dependency_exit(fail_on_body):
    from contextlib import AsyncExitStack, asynccontextmanager

    exits = []

    @asynccontextmanager
    async def transaction():
        try:
            yield
        except BaseException:
            exits.append("rollback")
            raise
        else:
            exits.append("commit")

    async def chunks():
        yield b"hello"

    stack = AsyncExitStack()
    await stack.enter_async_context(transaction())
    response = StreamLease(stack).transfer(
        StreamingResponse(chunks()),
    )

    async def send(message):
        if fail_on_body and message["type"] == "http.response.start":
            return
        raise OSError("transport unavailable")

    async def receive():
        await anyio.sleep_forever()

    with pytest.raises(ClientDisconnect):
        await response(
            {"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send
        )
    assert exits == ["rollback"]


@pytest.mark.anyio
async def test_stream_close_timeout_still_releases_dependencies(monkeypatch):
    from contextlib import AsyncExitStack, asynccontextmanager

    from opsmesh.plugins.channels.api import streaming
    from opsmesh.plugins.channels.api.streaming import ManagedStreamingResponse

    monkeypatch.setattr(streaming, "_CLEANUP_TIMEOUT_SECONDS", 0.02)
    exits = []

    @asynccontextmanager
    async def dependency():
        try:
            yield
        except TimeoutError:
            exits.append("rollback")
            raise

    async def chunks():
        yield b"hello"

    async def close_source():
        await anyio.sleep_forever()

    stack = AsyncExitStack()
    await stack.enter_async_context(dependency())
    response = StreamLease(stack).transfer(
        ManagedStreamingResponse(
            chunks(), on_close=lambda error: close_source()
        )
    )
    with pytest.raises(TimeoutError):
        await response.aclose()
    assert exits == ["rollback"]


@pytest.mark.anyio
async def test_stream_timeout_preserves_existing_failure(monkeypatch):
    from contextlib import AsyncExitStack, asynccontextmanager

    from opsmesh.plugins.channels.api import streaming
    from opsmesh.plugins.channels.api.streaming import ManagedStreamingResponse

    monkeypatch.setattr(streaming, "_CLEANUP_TIMEOUT_SECONDS", 0.02)
    failure = OSError("client disconnected")
    failures = []

    @asynccontextmanager
    async def dependency():
        try:
            yield
        except OSError as error:
            failures.append(error)
            raise

    async def chunks():
        yield b"hello"

    async def close_source():
        await anyio.sleep_forever()

    stack = AsyncExitStack()
    await stack.enter_async_context(dependency())
    response = StreamLease(stack).transfer(
        ManagedStreamingResponse(
            chunks(), on_close=lambda error: close_source()
        )
    )
    await response.aclose(failure)
    assert failures == [failure]


@pytest.mark.anyio
async def test_http_request_uses_framework_processors_rules_and_matcher(
    monkeypatch,
):
    from opsmesh.adapters import Event
    from opsmesh.dispatch import (
        event_preprocessor,
        processors,
        run_postprocessor,
    )
    from opsmesh.matchers import Matcher
    from opsmesh.plugins.channels.api import HTTPEvent
    from opsmesh.rules import Rule

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    observed = []
    before = set(processors._event_preprocessors)
    after = set(processors._run_postprocessors)

    @event_preprocessor
    async def preprocess(event: Event):
        observed.append(("pre", isinstance(event, HTTPEvent)))

    @run_postprocessor
    async def postprocess(matcher: Matcher):
        observed.append(("post", matcher.type))

    async def permitted(event: Event):
        observed.append(("rule", event.get_type()))
        return True

    async def handler(event: Event, caller: Annotated[UserContext, Context()]):
        observed.append(("handler", event.get_user_id() == caller.id))
        return {"ok": True}

    async def unrelated():
        raise AssertionError("HTTP request reached an unrelated matcher")

    alien = Matcher.new(handlers=[unrelated])
    router = APIRouter()
    declare_route(
        router,
        method="POST",
        path="/run",
        handler=handler,
        plugin_id="test",
        rule=Rule(permitted),
    )
    driver, service = await _setup(monkeypatch, router, make_settings())
    try:
        messages = await call(
            driver, scope("POST", "/plugins/test/run", token="secret")
        )
        assert payload(messages) == {"ok": True}
        assert observed == [
            ("pre", True),
            ("rule", "http"),
            ("handler", True),
            ("post", ""),
        ]
    finally:
        processors._event_preprocessors.intersection_update(before)
        processors._run_postprocessors.intersection_update(after)
        alien.destroy()
        await service.shutdown()


@pytest.mark.anyio
async def test_http_matcher_denial_does_not_invoke_handler(monkeypatch):
    from opsmesh.permissions import Permission

    monkeypatch.setenv("TEST_API_TOKEN", "secret")

    async def deny():
        return False

    async def handler():
        raise AssertionError("Denied handler was invoked")

    router = APIRouter()
    declare_route(
        router,
        method="GET",
        path="/private",
        handler=handler,
        plugin_id="test",
        permission=Permission(deny),
    )
    driver, service = await _setup(monkeypatch, router, make_settings())
    try:
        messages = await call(
            driver, scope("GET", "/plugins/test/private", token="secret")
        )
        assert messages[0]["status"] == 204
    finally:
        await service.shutdown()


@pytest.mark.anyio
async def test_http_dispatch_timeout_cleans_dependencies(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    closed = []

    async def resource():
        try:
            yield "open"
        finally:
            closed.append(True)

    async def handler(value: Annotated[str, Depends(resource)]):
        await anyio.sleep_forever()

    router = APIRouter()
    declare_route(
        router,
        method="GET",
        path="/wait",
        handler=handler,
        plugin_id="test",
    )
    settings = make_settings().model_copy(
        update={"request_timeout_seconds": 1.0}
    )
    driver, service = await _setup(monkeypatch, router, settings)
    try:
        messages = await call(
            driver, scope("GET", "/plugins/test/wait", token="secret")
        )
        assert messages[0]["status"] == 504
        assert closed == [True]
    finally:
        await service.shutdown()


def test_route_revocation_removes_its_framework_matcher():

    from opsmesh.matchers import matchers
    from opsmesh.plugins import registry

    owner = plugin_owner("rollback", ready=False)
    router = APIRouter()

    async def handler():
        return None

    token = registry._current_plugin.set(owner)
    try:
        registration = declare_route(
            router, method="GET", path="/probe", handler=handler
        )
    finally:
        registry._current_plugin.reset(token)
    matcher = registration.matcher
    assert matcher in matchers[matcher.priority]
    while owner.cleanups:
        owner.cleanups.pop()()
    assert matcher not in matchers[matcher.priority]
    assert router.registrations == ()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["postprocessor", "timeout", "encoding"])
@pytest.mark.parametrize("close_fails", [False, True])
async def test_unclaimed_response_stream_closes_before_dependencies(
    monkeypatch, failure, close_fails
):

    from opsmesh.dispatch import processors, run_postprocessor

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    events = []
    before = set(processors._run_postprocessors)

    async def resource():
        try:
            yield None
        finally:
            events.append("dependency closed")

    async def chunks():
        raise AssertionError("An unclaimed stream must not be consumed")
        yield b"unused"

    async def close(error):
        assert error is not None
        events.append("stream closed")
        if close_fails:
            raise RuntimeError("stream cleanup failed")

    async def handler(value: Annotated[Any, Depends(resource)]):
        return ManagedStreamingResponse(chunks(), on_close=close)

    @run_postprocessor
    async def postprocess():
        if failure == "postprocessor":
            raise ValueError("postprocessor failed")
        if failure == "timeout":
            await anyio.sleep_forever()

    router = APIRouter()
    declare_route(
        router,
        method="GET",
        path="/stream",
        handler=handler,
        result_type=EchoResult if failure == "encoding" else None,
        plugin_id="test",
    )
    settings = make_settings().model_copy(
        update={"request_timeout_seconds": 1.0}
    )
    driver, channel = await _setup(monkeypatch, router, settings)
    try:
        messages = await call(
            driver, scope("GET", "/plugins/test/stream", token="secret")
        )
        assert messages[0]["status"] == (504 if failure == "timeout" else 500)
        assert events == ["stream closed", "dependency closed"]
    finally:
        processors._run_postprocessors.intersection_update(before)
        await channel.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("question", ["/api_probe", "/api_probe hello"])
async def test_api_message_uses_framework_command_and_authenticated_context(
    monkeypatch,
    question,
):
    from opsmesh.params import CommandArg
    from opsmesh.rules import command
    from opsmesh.rules.checks import TrieRule

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()
    driver, channel = await _setup(
        monkeypatch, router, make_settings(identity())
    )
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
@pytest.mark.parametrize(
    "value",
    [
        {"metadata": {"message": "/delete"}, "enabled": False},
        ["one", 2, None],
        None,
    ],
)
async def test_api_preserves_structured_input_without_promoting_it_to_text(
    monkeypatch,
    value,
):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def handler(event: HTTPEvent):
        assert event.get_type() == "http"
        with pytest.raises(ValueError, match="do not contain"):
            event.get_message()
        return {"received": event.payload}

    registration = declare_route(
        router,
        method="POST",
        path="/data",
        handler=handler,
        plugin_id="support",
    )
    driver, channel = await _setup(monkeypatch, router, make_settings())
    try:
        response = await call(
            driver,
            scope(
                "POST",
                registration.path,
                token="secret",
                body=json.dumps(value).encode(),
            ),
        )
        assert response[0]["status"] == 200
        assert payload(response) == {"received": value}
    finally:
        await channel.shutdown()
        registration.matcher.destroy()


@pytest.mark.anyio
async def test_api_channel_can_reply_with_a_framework_message(monkeypatch):
    from opsmesh.adapters import Channel

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

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
                "content": [{"type": "text", "text": "hello"}],
                "data": None,
            }
        ]
    finally:
        await channel.shutdown()
        registration.message_handlers[0].destroy()


@pytest.mark.anyio
async def test_route_runs_only_declared_command_matchers_once(monkeypatch):
    from opsmesh.adapters import Channel
    from opsmesh.plugins import on_command, on_message
    from opsmesh.rules.checks import TrieRule

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()
    driver, service = await _setup(monkeypatch, router, make_settings())
    monkeypatch.setattr(TrieRule, "prefix", type(TrieRule.prefix)())
    owner = plugin_owner("support")
    token = _current_plugin.set(owner)
    seen = []
    try:
        selected = on_command("route_probe", block=True)
        unrelated = on_command("route_probe", block=True)

        @selected.handle()
        async def selected_handler(channel: Channel, event: Event):
            seen.append("command")
            await channel.send(event, structured_reply({"command": True}))

        @unrelated.handle()
        async def unrelated_handler():
            raise AssertionError("Undeclared command must not run")

        fallback = on_message(priority=10, block=True)

        @fallback.handle()
        async def fallback_handler(channel: Channel, event: Event):
            seen.append("fallback")
            await channel.send(
                event, structured_reply({"text": event.get_plaintext()})
            )

        registration = router.register(
            method="POST",
            path="/messages",
            body=api_module.MessageRequest,
            message_handlers=(selected, fallback),
        )
    finally:
        _current_plugin.reset(token)
    try:
        response = await call(
            driver,
            scope(
                "POST",
                registration.path,
                token="secret",
                body=b'{"message":"/route_probe"}',
            ),
        )
        assert payload(response)["messages"][0]["data"] == {"command": True}
        assert seen == ["command"]
        response = await call(
            driver,
            scope(
                "POST",
                registration.path,
                token="secret",
                body=b'{"message":"hello"}',
            ),
        )
        assert payload(response)["messages"][0]["data"] == {"text": "hello"}
        assert seen == ["command", "fallback"]
    finally:
        selected.destroy()
        unrelated.destroy()
        fallback.destroy()
        await service.shutdown()


def test_route_rejects_foreign_commands_and_ambiguous_permissions(monkeypatch):
    from opsmesh.matchers import Matcher, MatcherSource
    from opsmesh.permissions import Permission

    router = APIRouter()
    owner = plugin_owner("support")
    foreign = Matcher.new(block=True, source=MatcherSource(plugin_id="other"))
    own = Matcher.new(block=True, source=MatcherSource(plugin_id="support"))
    token = _current_plugin.set(owner)
    try:

        async def handler():
            return None

        with pytest.raises(RouteRegistrationError, match="belong"):
            router.register(
                method="POST",
                path="/messages",
                body=api_module.MessageRequest,
                message_handlers=(foreign,),
            )
        with pytest.raises(RouteRegistrationError, match="permissions"):
            router.register(
                method="POST",
                path="/permission",
                body=api_module.MessageRequest,
                message_handlers=(own,),
                permission=Permission(),
            )
    finally:
        _current_plugin.reset(token)
        foreign.destroy()
        own.destroy()


@pytest.mark.anyio
async def test_authenticated_upload_message_and_download_share_scope(
    monkeypatch,
):
    import httpx

    from opsmesh.plugins import registry

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    monkeypatch.setenv("OTHER_API_TOKEN", "other-secret")
    router = APIRouter()

    async def handler(channel: "Channel", event: HTTPEvent):
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
    settings = make_settings(
        IdentitySettings(id="alice", token_ref="TEST_API_TOKEN"),
        IdentitySettings(id="bob", token_ref="OTHER_API_TOKEN"),
    )
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
            assert (await client.get(path, headers=headers)).status_code == 404
    finally:
        await channel.shutdown()
    assert not channel.uploads._uploads


@pytest.mark.anyio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_api_sends_files_as_protected_download_references(
    monkeypatch, wrapped
):
    import httpx

    from opsmesh.adapters import Channel
    from opsmesh.plugins import registry
    from opsmesh.plugins.channels import Attachment, Message, MessageSegment

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def handler(channel: Channel, event: HTTPEvent):
        attachment = event.attachments.add_bytes(
            Attachment.create("report.txt", "text/plain"), b"report"
        )
        segment = MessageSegment.attachment(attachment)
        await channel.send(event, Message(segment) if wrapped else segment)

    registration = declare_route(
        router, method="GET", path="/report", handler=handler
    )
    monkeypatch.setattr(registry, "get_plugin", lambda name: registration.owner)
    _, channel = await _setup(monkeypatch, router, make_settings())
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=channel.app),
            base_url="http://test",
        ) as client:
            headers = {"authorization": "Bearer secret"}
            result = await client.get(
                "/plugins/support/report", headers=headers
            )
            assert result.status_code == 200
            path = result.json()[0]["download_url"]
            assert (await client.get(path)).status_code == 401
            download = await client.get(path, headers=headers)
            assert download.content == b"report"
            assert download.headers["x-content-type-options"] == "nosniff"
    finally:
        await channel.shutdown()


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
    router = APIRouter()

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
    router = APIRouter()

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
    router = APIRouter()

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
@pytest.mark.parametrize(
    "annotation",
    [
        JSONResponse,
        Annotated[JSONResponse, "native"],
        JSONResponse | dict[str, str],
    ],
)
async def test_native_response_subclass_contract(monkeypatch, annotation):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def handler():
        return JSONResponse({"native": True})

    registration = declare_route(
        router,
        method="GET",
        path="/native",
        handler=handler,
        result_type=annotation,
    )
    server, channel = await _setup(monkeypatch, router, make_settings())
    try:
        result = await call(
            server, scope(path=registration.path, token="secret")
        )
        assert payload(result) == {"native": True}
    finally:
        await channel.shutdown()


@pytest.mark.anyio
async def test_native_response_rejects_undeclared_subclass(monkeypatch):
    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def handler():
        return PlainTextResponse("private")

    registration = declare_route(
        router,
        method="GET",
        path="/native",
        handler=handler,
        result_type=JSONResponse,
    )
    server, channel = await _setup(monkeypatch, router, make_settings())
    try:
        result = await call(
            server, scope(path=registration.path, token="secret")
        )
        assert result[0]["status"] == 500
        assert "private" not in body(result).decode()
    finally:
        await channel.shutdown()


def test_message_binding_rejects_message_override_and_equal_priorities():
    from opsmesh.matchers import Matcher, MatcherSource

    class ChangedMessage(api_module.MessageRequest):
        message: str

    owner = plugin_owner("support")
    handlers = tuple(
        Matcher.new(
            type_="message",
            block=True,
            source=MatcherSource(plugin_id="support"),
        )
        for _ in range(2)
    )
    token = _current_plugin.set(owner)
    try:
        with pytest.raises(
            RouteRegistrationError, match="standard MessageRequest"
        ):
            APIRouter().register(
                method="POST",
                path="/override",
                body=ChangedMessage,
                message_handlers=handlers[:1],
            )
        with pytest.raises(RouteRegistrationError, match="distinct priorities"):
            APIRouter().register(
                method="POST",
                path="/messages",
                body=api_module.MessageRequest,
                message_handlers=handlers,
            )
    finally:
        _current_plugin.reset(token)
        for handler in handlers:
            handler.destroy()


@pytest.mark.anyio
async def test_image_only_api_request_stays_a_framework_message(monkeypatch):
    from opsmesh.plugins.channels import Attachment

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

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
@pytest.mark.parametrize("message_route", [False, True])
@pytest.mark.parametrize("streamed", [False, True])
async def test_admitted_request_continues_after_owner_starts_stopping(
    monkeypatch, message_route, streamed
):
    from opsmesh.plugins.invocation import plugin_call

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()
    closed = []
    registration = None

    async def resource():
        try:
            yield "resource"
        finally:
            async with plugin_call(registration.owner.id_):
                closed.append(True)

    dependency = Depends(resource)

    async def handler(channel: Channel, event: Event, value=dependency):
        assert value == "resource"
        assert registration.owner.status is PluginStatus.STOPPING
        assert len(registration.owner.calls) == 2
        async with plugin_call(registration.owner.id_):
            assert len(registration.owner.calls) == 2
        if streamed:

            async def chunks():
                assert registration.owner.calls and not closed
                yield b"continued"

            if message_route:

                async def text_chunks():
                    assert registration.owner.calls and not closed
                    yield "continued"

                await channel.call("stream", event=event, source=text_chunks())
            else:
                await channel.send(event, StreamingResponse(chunks()))
        else:
            await channel.send(
                event,
                "continued"
                if message_route
                else json_response({"continued": True}),
            )

    if message_route:
        registration = declare_message(router, handler)
    else:
        registration = declare_route(
            router, method="POST", path="/continue", handler=handler
        )
    server, channel = await _setup(monkeypatch, router, make_settings())
    parse = channel._parse_body

    async def parse_while_stopping(route, request):
        assert len(registration.owner.calls) == 1
        registration.owner.status = PluginStatus.STOPPING
        return await parse(route, request)

    monkeypatch.setattr(channel, "_parse_body", parse_while_stopping)
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
        if message_route:
            assert payload(result)["messages"][0]["content"] == [
                {"type": "text", "text": "continued"}
            ]
        else:
            assert body(result) == (
                b"continued" if streamed else b'{"continued":true}'
            )
        assert closed == [True] and not registration.owner.calls
    finally:
        if registration.matcher is not None:
            registration.matcher.destroy()
        for matcher in registration.message_handlers:
            matcher.destroy()
        await channel.shutdown()


@pytest.mark.anyio
@pytest.mark.parametrize("asgi_version", ["2.0", "2.4"])
async def test_response_stream_keeps_admission_without_granting_child_tasks(
    monkeypatch, asgi_version
):
    import asyncio

    from starlette.background import BackgroundTask

    from opsmesh.plugins.invocation import PluginUnavailable, plugin_call
    from opsmesh.plugins.registry import get_current_plugin

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()
    events = []
    registration = None

    async def independent_call():
        with pytest.raises(PluginUnavailable):
            async with plugin_call(registration.owner.id_):
                raise AssertionError("A child task must obtain new admission")

    async def record(stage):
        assert get_current_plugin() is registration.owner
        async with plugin_call(registration.owner.id_):
            assert len(registration.owner.calls) == 2
            events.append(stage)

    async def chunks():
        try:
            await record("stream")
            await asyncio.create_task(independent_call())
            yield b"continued"
        finally:
            await record("source closed")

    async def handler():
        registration.owner.status = PluginStatus.STOPPING
        return StreamingResponse(
            chunks(), background=BackgroundTask(record, "background")
        )

    registration = declare_route(
        router, method="GET", path="/stream-scope", handler=handler
    )
    server, channel = await _setup(monkeypatch, router, make_settings())
    request_scope, queue = scope(path=registration.path, token="secret")
    request_scope["asgi"] = {"spec_version": asgi_version}
    try:
        result = await call(server, (request_scope, queue))
        assert body(result) == b"continued"
        assert events == ["stream", "source closed", "background"]
        assert not registration.owner.calls
        assert get_current_plugin() is None
    finally:
        registration.matcher.destroy()
        await channel.shutdown()
