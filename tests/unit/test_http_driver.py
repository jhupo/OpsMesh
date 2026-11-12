"""Exercise the ASGI HTTP driver transport contract."""

import json
import logging
import threading
import time
import urllib.request

import anyio
import pytest
import uvicorn

from opsmesh.drivers.http import HTTPDriver
from opsmesh.framework.config import Config, Env
from opsmesh.framework.drivers import (
    URL,
    HTTPServerSetup,
    Response,
    ResponseStream,
    WebSocketServerSetup,
)


def new_driver(**options):
    defaults = {"enabled": False}
    defaults.update(options)
    return HTTPDriver(
        Env(),
        Config(driver_options={"http": defaults}),
    )


def http_scope(method="GET", path="/", query=b"", headers=(), server=None):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query,
        "headers": list(headers),
        "scheme": "http",
        "server": server or ("testserver", 80),
    }


async def call(driver, scope, messages=()):
    sent = []
    queue = list(messages) or [{"type": "http.request", "body": b""}]

    async def receive():
        if queue:
            return queue.pop(0)
        await anyio.sleep_forever()

    async def send(message):
        sent.append(message)

    await driver(scope, receive, send)
    return sent


def body(messages):
    return b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )


def test_buffered_response_uses_framework_types():
    async def scenario():
        driver = new_driver()

        async def handler(request):
            assert request.url.path == "/hello"
            return Response(200, content="world")

        driver.setup_http_server(
            HTTPServerSetup(URL("/hello"), "GET", "hello", handler)
        )
        return await call(driver, http_scope(path="/hello"))

    messages = anyio.run(scenario)
    start = messages[0]
    assert start["status"] == 200
    assert (b"content-type", b"text/plain; charset=utf-8") in start["headers"]
    assert body(messages) == b"world"
    assert messages[-1]["more_body"] is False


def test_request_parses_query_headers_cookies_and_body():
    async def scenario():
        driver = new_driver()

        async def handler(request):
            assert request.method == "POST"
            assert request.url.query["name"] == "value"
            assert request.headers["x-test"] == "yes"
            assert request.cookies.get("session") == "abc"
            assert request.content == b"payload"
            return Response(201, content=b"ok")

        driver.setup_http_server(
            HTTPServerSetup(URL("/submit"), "post", "submit", handler)
        )
        return await call(
            driver,
            http_scope(
                method="POST",
                path="/submit",
                query=b"name=value",
                headers=[
                    (b"x-test", b"yes"),
                    (b"cookie", b"session=abc; theme=dark"),
                ],
            ),
            [{"type": "http.request", "body": b"payload"}],
        )

    messages = anyio.run(scenario)
    assert messages[0]["status"] == 201
    assert body(messages) == b"ok"


def test_unknown_route_and_method_mismatch_are_explicit():
    async def scenario():
        driver = new_driver()

        async def handler(request):
            return Response(200)

        driver.setup_http_server(
            HTTPServerSetup(URL("/known"), "GET", "known", handler)
        )
        missing = await call(driver, http_scope(path="/missing"))
        mismatch = await call(driver, http_scope(method="POST", path="/known"))
        return missing, mismatch

    missing, mismatch = anyio.run(scenario)
    assert missing[0]["status"] == 404
    assert mismatch[0]["status"] == 405
    assert (b"allow", b"GET, HEAD") in mismatch[0]["headers"]


def test_head_uses_get_handler_without_a_body():
    async def scenario():
        driver = new_driver()

        async def handler(request):
            return Response(200, content="hidden")

        driver.setup_http_server(
            HTTPServerSetup(URL("/page"), "GET", "page", handler)
        )
        return await call(driver, http_scope(method="HEAD", path="/page"))

    messages = anyio.run(scenario)
    assert messages[0]["status"] == 200
    assert body(messages) == b""


def test_oversized_request_body_is_rejected_before_the_handler():
    async def scenario():
        driver = new_driver(max_request_body=4)
        called = False

        async def handler(request):
            nonlocal called
            called = True
            return Response(200)

        driver.setup_http_server(
            HTTPServerSetup(URL("/body"), "POST", "body", handler)
        )
        messages = await call(
            driver,
            http_scope(method="POST", path="/body"),
            [{"type": "http.request", "body": b"12345678"}],
        )
        return called, messages

    called, messages = anyio.run(scenario)
    assert called is False
    assert messages[0]["status"] == 413


def test_handler_failure_returns_a_safe_500_response():
    async def scenario():
        driver = new_driver()

        async def handler(request):
            raise RuntimeError("secret detail")

        driver.setup_http_server(
            HTTPServerSetup(URL("/fail"), "GET", "fail", handler)
        )
        return await call(driver, http_scope(path="/fail"))

    messages = anyio.run(scenario)
    assert messages[0]["status"] == 500
    assert b"secret detail" not in body(messages)


def test_stream_is_forwarded_and_closed_once():
    class Stream:
        def __init__(self):
            self.items = iter([b"first", b"second"])
            self.closed = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.items)
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self):
            self.closed += 1

    async def scenario():
        driver = new_driver()
        stream = Stream()

        async def handler(request):
            return Response(200, content=ResponseStream(stream))

        driver.setup_http_server(
            HTTPServerSetup(URL("/stream"), "GET", "stream", handler)
        )
        messages = await call(driver, http_scope(path="/stream"))
        return stream, messages

    stream, messages = anyio.run(scenario)
    chunks = [
        message["body"]
        for message in messages
        if message["type"] == "http.response.body" and message.get("body")
    ]
    assert chunks == [b"first", b"second"]
    assert stream.closed == 1
    assert messages[-1]["more_body"] is False


def test_stream_failure_still_closes_and_ends_the_response(caplog):
    class BrokenStream:
        def __init__(self):
            self.sent = 0
            self.closed = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.sent == 0:
                self.sent += 1
                return b"partial"
            raise RuntimeError("stream broke")

        async def aclose(self):
            self.closed += 1

    async def scenario():
        driver = new_driver()
        stream = BrokenStream()

        async def handler(request):
            return Response(200, content=ResponseStream(stream))

        driver.setup_http_server(
            HTTPServerSetup(URL("/broken"), "GET", "broken", handler)
        )
        with caplog.at_level(logging.ERROR):
            messages = await call(driver, http_scope(path="/broken"))
        return stream, messages

    stream, messages = anyio.run(scenario)
    assert stream.closed == 1
    assert messages[0]["status"] == 200
    assert messages[-1]["more_body"] is False
    assert any("stream failed" in item.message for item in caplog.records)


def test_stream_closes_when_response_start_fails():
    class Stream:
        def __init__(self):
            self.closed = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise AssertionError("the stream must not be consumed")

        async def aclose(self):
            self.closed += 1

    async def scenario():
        driver = new_driver()
        stream = Stream()

        async def handler(request):
            return Response(200, content=ResponseStream(stream))

        driver.setup_http_server(
            HTTPServerSetup(URL("/stream"), "GET", "stream", handler)
        )
        messages = [{"type": "http.request", "body": b""}]

        async def receive():
            if messages:
                return messages.pop(0)
            await anyio.sleep_forever()

        async def send(message):
            if message["type"] == "http.response.start":
                raise RuntimeError("header send failed")

        await driver(http_scope(path="/stream"), receive, send)
        return stream

    stream = anyio.run(scenario)
    assert stream.closed == 1


def test_stream_closes_when_cancelled_before_response_start():
    class Stream:
        def __init__(self):
            self.closed = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise AssertionError("the stream must not be consumed")

        async def aclose(self):
            self.closed += 1

    async def scenario():
        driver = new_driver()
        stream = Stream()

        async def handler(request):
            return Response(200, content=ResponseStream(stream))

        driver.setup_http_server(
            HTTPServerSetup(URL("/stream"), "GET", "stream", handler)
        )
        messages = [{"type": "http.request", "body": b""}]

        async def receive():
            if messages:
                return messages.pop(0)
            await anyio.sleep_forever()

        async def send(message):
            await anyio.sleep_forever()

        with anyio.move_on_after(0.1):
            await driver(http_scope(path="/stream"), receive, send)
        return stream

    stream = anyio.run(scenario)
    assert stream.closed == 1


def test_route_registration_rejects_conflicts_and_invalid_paths():
    driver = new_driver()

    async def handler(request):
        return Response(200)

    driver.setup_http_server(
        HTTPServerSetup(URL("/same"), "GET", "same", handler)
    )
    with pytest.raises(ValueError, match="Duplicate HTTP route"):
        driver.setup_http_server(
            HTTPServerSetup(URL("/same"), "GET", "same", handler)
        )
    with pytest.raises(ValueError, match="must start with"):
        driver.setup_http_server(
            HTTPServerSetup(URL("relative"), "GET", "relative", handler)
        )
    with pytest.raises(ValueError, match="relative URL paths"):
        driver.setup_http_server(
            HTTPServerSetup(URL("https://example.com/a"), "GET", "a", handler)
        )


def test_template_routes_expose_path_params_and_prefer_exact_paths():
    async def scenario():
        driver = new_driver()

        async def template(request):
            return Response(200, content=json.dumps(request.path_params))

        async def exact(request):
            return Response(200, content="exact")

        driver.setup_http_server(
            HTTPServerSetup(
                URL("/plugins/s/agents/{agent_key}/sessions/{session_id}"),
                "GET",
                "template",
                template,
            )
        )
        driver.setup_http_server(
            HTTPServerSetup(
                URL("/plugins/s/agents/fixed"), "GET", "exact", exact
            )
        )
        matched = await call(
            driver, http_scope(path="/plugins/s/agents/a/sessions/s-1")
        )
        overlapping = await call(
            driver, http_scope(path="/plugins/s/agents/fixed")
        )
        mismatch = await call(
            driver,
            http_scope(
                method="POST",
                path="/plugins/s/agents/a/sessions/s-1",
            ),
        )
        return matched, overlapping, mismatch

    matched, overlapping, mismatch = anyio.run(scenario)
    assert json.loads(body(matched)) == {
        "agent_key": "a",
        "session_id": "s-1",
    }
    assert body(overlapping) == b"exact"
    assert mismatch[0]["status"] == 405
    assert (b"allow", b"GET, HEAD") in mismatch[0]["headers"]


def test_ambiguous_templates_and_removed_routes_are_explicit():
    driver = new_driver()

    async def handler(request):
        return Response(200)

    driver.setup_http_server(
        HTTPServerSetup(URL("/a/{one}"), "GET", "one", handler)
    )
    with pytest.raises(ValueError, match="Duplicate HTTP route"):
        driver.setup_http_server(
            HTTPServerSetup(URL("/a/{two}"), "GET", "two", handler)
        )
    assert driver.remove_http_server("GET", "/a/{one}") is True
    assert driver.remove_http_server("GET", "/a/{one}") is False


def test_disconnect_before_the_first_byte_cancels_the_handler():
    async def scenario():
        driver = new_driver()
        cancelled = anyio.Event()

        async def handler(request):
            try:
                await anyio.sleep_forever()
            finally:
                cancelled.set()
            return Response(200)

        driver.setup_http_server(
            HTTPServerSetup(URL("/slow"), "GET", "slow", handler)
        )
        messages = await call(
            driver,
            http_scope(path="/slow"),
            [
                {"type": "http.request", "body": b""},
                {"type": "http.disconnect"},
            ],
        )
        return cancelled, messages

    cancelled, messages = anyio.run(scenario)
    assert cancelled.is_set()
    assert not [
        message
        for message in messages
        if message["type"] == "http.response.start"
    ]


def test_websocket_route_keeps_its_own_registration_namespace():
    driver = new_driver()

    async def handler(websocket):
        return None

    driver.setup_websocket_server(
        WebSocketServerSetup(URL("/socket"), "socket", handler)
    )
    with pytest.raises(ValueError, match="Duplicate WebSocket route"):
        driver.setup_websocket_server(
            WebSocketServerSetup(URL("/socket"), "socket", handler)
        )


@pytest.mark.anyio
async def test_listener_binds_an_ephemeral_port_and_drains_on_shutdown():
    driver = HTTPDriver(
        Env(),
        Config(
            driver_options={
                "http": {"host": "127.0.0.1", "port": 0, "enabled": True}
            }
        ),
    )

    async def handler(request):
        return Response(
            200,
            headers={"content-type": "application/json"},
            content=json.dumps({"status": "ok"}),
        )

    driver.setup_http_server(
        HTTPServerSetup(URL("/health"), "GET", "health", handler)
    )
    async with driver.lifespan():
        port = driver.bound_port
        assert port is not None

        def fetch():
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health", timeout=5
            ) as response:
                return response.status, response.read()

        status, payload = await anyio.to_thread.run_sync(fetch)
        assert status == 200
        assert json.loads(payload) == {"status": "ok"}
    assert driver.bound_port is None


@pytest.mark.anyio
async def test_listener_startup_failure_rolls_back_resources(monkeypatch):
    driver = HTTPDriver(
        Env(),
        Config(
            driver_options={
                "http": {
                    "host": "127.0.0.1",
                    "port": 0,
                    "enabled": True,
                    "shutdown_timeout": 0.2,
                }
            }
        ),
    )
    sockets = []
    original_bind = driver._bind_socket

    def bind():
        sock = original_bind()
        sockets.append(sock)
        return sock

    monkeypatch.setattr(driver, "_bind_socket", bind)
    opened = []
    driver.on_startup(lambda: opened.append("open"))
    driver.on_shutdown(lambda: opened.pop())

    async def fail_startup(self, sockets=None):
        raise OSError("address already in use")

    monkeypatch.setattr(uvicorn.Server, "startup", fail_startup)
    with pytest.raises(OSError, match="address already in use"):
        await driver.startup()

    assert driver.started is False
    assert driver.bound_port is None
    assert opened == []
    assert sockets and sockets[0].fileno() == -1


@pytest.mark.anyio
async def test_shutdown_cancels_an_endless_stream_at_the_deadline():
    driver = HTTPDriver(
        Env(),
        Config(
            driver_options={
                "http": {
                    "host": "127.0.0.1",
                    "port": 0,
                    "enabled": True,
                    "shutdown_timeout": 0.3,
                }
            }
        ),
    )

    class EndlessStream:
        def __init__(self):
            self.sent = False
            self.closed = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.sent:
                self.sent = True
                return b"tick"
            await anyio.sleep_forever()

        async def aclose(self):
            self.closed += 1

    stream = EndlessStream()
    plugin_closed = []
    driver.on_shutdown(lambda: plugin_closed.append("closed"))

    async def handler(request):
        return Response(200, content=ResponseStream(stream))

    driver.setup_http_server(
        HTTPServerSetup(URL("/stream"), "GET", "stream", handler)
    )
    await driver.startup()
    port = driver.bound_port
    assert port is not None

    chunks = []
    errors = []
    release = threading.Event()

    def fetch():
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/stream", timeout=10
            ) as response:
                chunks.append(response.read(4))
                release.wait(10)
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=fetch, daemon=True)
    thread.start()
    try:
        with anyio.fail_after(5):
            while not chunks and not errors:
                await anyio.sleep(0.02)
        assert chunks == [b"tick"]
        started = time.monotonic()
        await driver.shutdown()
        assert time.monotonic() - started < 3
        assert driver.bound_port is None
        assert plugin_closed == ["closed"]
        with anyio.fail_after(5):
            while stream.closed == 0:
                await anyio.sleep(0.02)
    finally:
        release.set()
        await anyio.to_thread.run_sync(thread.join, 5)

    assert errors == []
    assert stream.closed == 1
