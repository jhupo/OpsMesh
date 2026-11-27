"""Driver composition, Cookies/Request models, FastAPI ASGI, and example E2E."""

from pathlib import Path
import sys

import httpx
import pytest

import opsmesh
from opsmesh import _resolve_combine_expr
from opsmesh.adapters import Adapter, Gateway
from opsmesh.config import Config, Env
from opsmesh.drivers import (
    URL,
    Cookies,
    HTTPServerSetup,
    Request,
    Response,
    combine_driver,
)
from opsmesh.drivers.fastapi import Driver as FastAPIDriver
from opsmesh.drivers.httpx import Mixin as HTTPXMixin
from opsmesh.drivers.none import Driver as NoneDriver
from opsmesh.message import _event_preprocessors
from opsmesh.plugin import _managers, _plugins
from opsmesh.rule import TrieRule

EXAMPLE_DIR = Path(__file__).resolve().parents[2] / "examples" / "webhook_ops"


class _MinimalAdapter(Adapter):
    @classmethod
    def get_name(cls) -> str:
        return "minimal"

    async def _invoke(self, gateway: Gateway, action: str, **params):
        return None


def test_combine_driver_and_resolve_expr():
    cls1 = combine_driver(FastAPIDriver, HTTPXMixin)
    cls2 = _resolve_combine_expr("~fastapi+~httpx")
    assert issubclass(cls1, FastAPIDriver)
    assert issubclass(cls1, HTTPXMixin)
    assert issubclass(cls2, FastAPIDriver)
    assert issubclass(cls2, HTTPXMixin)
    driver = cls2(Env(), Config())
    assert "fastapi" in driver.type
    assert "httpx" in driver.type


def test_cookies_and_request_header_generation():
    cookies = Cookies({"session": "abc"})
    cookies.set("theme", "dark", domain="example.com", path="/")
    assert cookies["session"] == "abc"
    assert len(cookies) == 2

    req = Request("GET", "https://example.com/api", cookies=cookies)
    header = cookies.as_header(req)
    assert "Cookie" in header
    assert "session=abc" in header["Cookie"]

    cookies.delete("session")
    assert "session" not in cookies


@pytest.mark.anyio
async def test_adapter_unsupported_mixin_checks():
    driver = NoneDriver(Env(), Config())
    adapter = _MinimalAdapter(driver)

    with pytest.raises(TypeError, match="http server"):
        adapter.setup_http_server(
            HTTPServerSetup(URL("/x"), "GET", "x", lambda r: Response(200))  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="http client"):
        await adapter.request(Request("GET", "https://example.com"))
    with pytest.raises(TypeError, match="http client"):
        adapter.get_session()
    with pytest.raises(TypeError, match="websocket client"):
        async with adapter.websocket(Request("GET", "ws://example.com")):
            pass


@pytest.mark.anyio
async def test_fastapi_driver_http_route_and_example_webhook_ops_e2e(monkeypatch):
    monkeypatch.syspath_prepend(str(EXAMPLE_DIR))
    monkeypatch.chdir(EXAMPLE_DIR)

    plugins_before = dict(_plugins)
    managers_before = list(_managers)
    pre_before = set(_event_preprocessors)
    monkeypatch.setattr(opsmesh, "_driver", None)

    import main as example_main

    try:
        example_main.bootstrap()
        driver = opsmesh.get_driver()
        assert isinstance(driver, FastAPIDriver)

        async with driver._lifespan_manager(driver.server_app):
            transport = httpx.ASGITransport(app=driver.asgi)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                # 1. Start multi-turn /deploy
                r1 = await client.post(
                    "/webhook/events",
                    json={
                        "event_type": "message",
                        "sender_id": "ops_root",
                        "channel_id": "ops",
                        "content": "/deploy",
                    },
                )
                assert r1.status_code == 200
                assert "请输入待发布服务名" in r1.json()["replies"][0]

                # 2. Invalid service triggers reject
                r2 = await client.post(
                    "/webhook/events",
                    json={
                        "event_type": "message",
                        "sender_id": "ops_root",
                        "channel_id": "ops",
                        "content": "invalid",
                    },
                )
                assert r2.status_code == 200
                assert "仅支持 api 或 worker" in r2.json()["replies"][0]

                # 3. Valid service completes deploy via Gateway.invoke
                r3 = await client.post(
                    "/webhook/events",
                    json={
                        "event_type": "message",
                        "sender_id": "ops_root",
                        "channel_id": "ops",
                        "content": "api",
                    },
                )
                assert r3.status_code == 200
                assert "已触发 api 发布至 prod" in r3.json()["replies"][0]

                # 4. Notice alert hits alert_observer plugin
                r4 = await client.post(
                    "/webhook/events",
                    json={
                        "event_type": "notice",
                        "sender_id": "monitor",
                        "channel_id": "alerts",
                        "content": "cpu > 90%",
                    },
                )
                assert r4.status_code == 200
                assert r4.json()["replies"] == [
                    "alert recorded: [alerts/monitor] cpu > 90%"
                ]
    finally:
        for plugin in list(_plugins.values()):
            if plugin.id_ not in plugins_before:
                for m in list(plugin.matcher):
                    try:
                        m.destroy()
                    except ValueError:
                        pass
        _plugins.clear()
        _plugins.update(plugins_before)
        _managers[:] = managers_before
        _event_preprocessors.difference_update(set(_event_preprocessors) - pre_before)
        TrieRule.prefix.pop("/deploy", None)
        for mod in list(sys.modules):
            if mod in {"main", "adapter"} or mod.startswith(
                ("plugins.", "opsmesh.plugins.")
            ):
                sys.modules.pop(mod, None)
