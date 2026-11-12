"""Check the API channel registration, auth, and HTTP invocation contract."""

import json
from types import ModuleType, SimpleNamespace
from typing import Annotated, Any

import anyio
import pytest
from pydantic import BaseModel

import opsmesh_channels.api as api_module
from opsmesh.drivers.http import HTTPDriver
from opsmesh.framework import runtime
from opsmesh.framework.adapters import Event
from opsmesh.framework.config import Config, Env
from opsmesh.framework.drivers import Response
from opsmesh.framework.messages import Message
from opsmesh.framework.params import Context, Depends, EventMessage
from opsmesh.framework.plugins.model import Plugin, PluginStatus
from opsmesh.framework.plugins.registry import _current_plugin
from opsmesh_channels import UserContext
from opsmesh_channels.api import (
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
from opsmesh_channels.api.routes import normalize_relative_path
from opsmesh_channels.api.streaming import StreamLease


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


def make_driver():
    return HTTPDriver(
        Env(), Config(driver_options={"http": {"enabled": False}})
    )


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


async def call(driver, request):
    request_scope, queue = request
    sent = []

    async def receive():
        if queue:
            return queue.pop(0)
        await anyio.sleep_forever()

    async def send(message):
        sent.append(message)

    await driver(request_scope, receive, send)
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
    driver = make_driver()
    monkeypatch.setattr(runtime, "_driver", driver)
    service = APIChannel(settings, router)
    await service.startup()
    return driver, service


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
        message: Annotated[Message, EventMessage()],
        caller: Annotated[UserContext, Context()],
    ) -> EchoResult:
        return EchoResult(
            text=message["data"][0].data["value"]["text"], caller=caller.id
        )

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
        assert (b"allow", b"GET, HEAD") in mismatch[0]["headers"]
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
    driver = make_driver()
    monkeypatch.setattr(runtime, "_driver", driver)
    service = APIChannel(make_settings(identity()), APIRouter())
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
    owner = SimpleNamespace(id_="support", ready=False, cleanups=[])
    token = _current_plugin.set(owner)
    try:

        @api_module.post("/echo", body=EchoBody)
        async def handler(
            message: Annotated[Message, EventMessage()],
            caller: Annotated[UserContext, Context()],
        ) -> EchoResult:
            return EchoResult(
                text=message["data"][0].data["value"]["text"],
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

        owner.ready = True
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
    owner = SimpleNamespace(id_="support", ready=True, cleanups=[])
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
        non_ascii = await call(driver, scope(path=path, token="密钥"))
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
        "from opsmesh.framework.adapters import Event\n"
        "class Body(BaseModel):\n    text: str\n"
        "class Reply(BaseModel):\n    text: str\n"
        "async def handler(event: Event) -> Reply:\n"
        '    value = event.get_message()["data"][0].data["value"]\n'
        '    return Reply(text=value["text"])\n',
        module.__dict__,
    )
    router = APIRouter()
    monkeypatch.setattr(api_module.routing, "_router", router)
    owner = SimpleNamespace(id_="postponed", ready=True, cleanups=[])
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
    from opsmesh.framework.drivers import Response

    router = APIRouter()

    async def handler():
        return Response(200, content="unvalidated")

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

    from opsmesh.framework.drivers import Response, ResponseStream

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
        Response(200, content=ResponseStream(chunks())),
    )

    async def send(message):
        if fail_on_body and message["type"] == "http.response.start":
            return
        raise OSError("transport unavailable")

    await make_driver()._send_streamed(send, response, head=False)
    assert exits == ["rollback"]


@pytest.mark.anyio
async def test_stream_close_timeout_still_releases_dependencies(monkeypatch):
    from contextlib import AsyncExitStack, asynccontextmanager

    from opsmesh.framework.drivers import ResponseStream
    from opsmesh_channels.api import streaming

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
        Response(200, content=ResponseStream(chunks(), close=close_source))
    )
    with pytest.raises(TimeoutError):
        await response.content.aclose()
    assert exits == ["rollback"]


@pytest.mark.anyio
async def test_stream_timeout_preserves_existing_failure(monkeypatch):
    from contextlib import AsyncExitStack, asynccontextmanager

    from opsmesh.framework.drivers import ResponseStream
    from opsmesh_channels.api import streaming

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
        Response(200, content=ResponseStream(chunks(), close=close_source))
    )
    await response.content.aclose(failure)
    assert failures == [failure]


@pytest.mark.anyio
async def test_http_request_uses_framework_processors_rules_and_matcher(
    monkeypatch,
):
    from opsmesh.framework.adapters import Event
    from opsmesh.framework.dispatch import (
        event_preprocessor,
        processors,
        run_postprocessor,
    )
    from opsmesh.framework.matchers import Matcher
    from opsmesh.framework.rules import Rule
    from opsmesh_channels.api import HTTPEvent

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
    from opsmesh.framework.permissions import Permission

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
        assert messages[0]["status"] == 403
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
        update={"request_timeout_seconds": 0.03}
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
    from types import SimpleNamespace

    from opsmesh.framework.matchers import matchers
    from opsmesh.framework.plugins import registry

    owner = SimpleNamespace(id_="rollback", ready=False, cleanups=[])
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
    from opsmesh.framework.dispatch import processors, run_postprocessor
    from opsmesh.framework.drivers import Response, ResponseStream

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
        return Response(200, content=ResponseStream(chunks(), on_exit=close))

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
        update={"request_timeout_seconds": 0.05}
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
    from opsmesh.framework.params import CommandArg
    from opsmesh.framework.rules import command
    from opsmesh.framework.rules.checks import TrieRule

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()
    driver, channel = await _setup(
        monkeypatch, router, make_settings(identity())
    )
    monkeypatch.setattr(TrieRule, "prefix", type(TrieRule.prefix)())

    async def handler(
        event: Event,
        message: Annotated[Message, EventMessage()],
        argument: Annotated[Message, CommandArg()],
        caller: Annotated[UserContext, Context()],
    ):
        assert event.get_user_id() == caller.id
        assert event.get_type() == "message"
        assert message.extract_plain_text() == question
        assert len(message) == 1
        return {
            "argument": argument.extract_plain_text(),
            "id": caller.id,
            "roles": sorted(caller.roles),
            "groups": sorted(caller.groups),
        }

    registration = declare_route(
        router,
        method="POST",
        path="/command",
        handler=handler,
        plugin_id="support",
        rule=command("api_probe", force_whitespace=True),
    )
    try:
        response = await call(
            driver,
            scope(
                "POST",
                registration.path,
                token="secret",
                body=json.dumps(
                    {
                        "message": question,
                        "id": "forged",
                        "roles": ["admin"],
                        "groups": ["forged"],
                    }
                ).encode(),
            ),
        )
        assert response[0]["status"] == 200
        assert payload(response) == {
            "argument": question.removeprefix("/api_probe").strip(),
            "id": "api:user",
            "roles": ["agent"],
            "groups": ["support"],
        }
    finally:
        await channel.shutdown()
        registration.matcher.destroy()


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

    async def handler(
        event: Event, message: Annotated[Message, EventMessage()]
    ):
        assert event.get_type() == "http"
        assert event.get_plaintext() == ""
        return {"received": message["data"][0].data["value"]}

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
    from opsmesh.framework.adapters import Channel

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()

    async def handler(
        channel: Channel,
        event: Event,
        message: Annotated[Message, EventMessage()],
    ):
        await channel.send(event, message)

    registration = declare_route(
        router,
        method="POST",
        path="/echo",
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
                body=b'"hello"',
            ),
        )
        assert response[0]["status"] == 200
        assert payload(response) == [
            {"type": "text", "data": {"text": "hello"}}
        ]
    finally:
        await channel.shutdown()
        registration.matcher.destroy()


@pytest.mark.anyio
async def test_route_runs_only_declared_command_matchers_once(monkeypatch):
    from opsmesh.framework.adapters import Channel
    from opsmesh.framework.plugins import on_command
    from opsmesh.framework.rules.checks import TrieRule

    monkeypatch.setenv("TEST_API_TOKEN", "secret")
    router = APIRouter()
    driver, service = await _setup(monkeypatch, router, make_settings())
    monkeypatch.setattr(TrieRule, "prefix", type(TrieRule.prefix)())
    owner = SimpleNamespace(
        id_="support", ready=True, cleanups=[], matcher=set()
    )
    token = _current_plugin.set(owner)
    seen = []
    try:
        selected = on_command("route_probe", block=True)
        unrelated = on_command("route_probe", block=True)

        @selected.handle()
        async def selected_handler(channel: Channel, event: Event):
            seen.append("command")
            await channel.send(event, {"command": True})

        @unrelated.handle()
        async def unrelated_handler():
            raise AssertionError("Undeclared command must not run")

        async def fallback(event: Event):
            seen.append("fallback")
            return {"text": event.get_plaintext()}

        registration = declare_route(
            router,
            method="POST",
            path="/message",
            handler=fallback,
            message_matchers=(selected,),
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
        assert payload(response) == {"command": True}
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
        assert payload(response) == {"text": "hello"}
        assert seen == ["command", "fallback"]
    finally:
        selected.destroy()
        unrelated.destroy()
        registration.matcher.destroy()
        await service.shutdown()


def test_route_rejects_foreign_commands_and_ambiguous_permissions(monkeypatch):
    from opsmesh.framework.matchers import Matcher, MatcherSource
    from opsmesh.framework.permissions import Permission

    router = APIRouter()
    owner = SimpleNamespace(id_="support", ready=True, cleanups=[])
    foreign = Matcher.new(block=True, source=MatcherSource(plugin_id="other"))
    own = Matcher.new(block=True, source=MatcherSource(plugin_id="support"))
    token = _current_plugin.set(owner)
    try:

        async def handler():
            return None

        with pytest.raises(RouteRegistrationError, match="belong"):
            declare_route(
                router,
                method="POST",
                path="/foreign",
                handler=handler,
                message_matchers=(foreign,),
            )
        with pytest.raises(RouteRegistrationError, match="permissions"):
            declare_route(
                router,
                method="POST",
                path="/permission",
                handler=handler,
                message_matchers=(own,),
                permission=Permission(),
            )
    finally:
        _current_plugin.reset(token)
        foreign.destroy()
        own.destroy()
