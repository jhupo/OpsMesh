"""Platform startup loads plugins before mounting Driver ingress under body boundaries."""

import sys
from textwrap import dedent

from httpx import ASGITransport, AsyncClient

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.entrypoints.http.app import create_http_app
from opsmesh.framework.plugin import manager


async def test_plugin_ingress_mounts_after_loading_with_platform_body_limit(tmp_path, monkeypatch):
    name = "http_ingress_example"
    (tmp_path / f"{name}.py").write_text(
        dedent("""
            from opsmesh.framework import get_driver
            from opsmesh.framework.drivers import HTTPServerSetup, HTTPResponse

            calls = []

            async def echo(request):
                calls.append(request.content)
                return HTTPResponse(200, {}, request.content)

            get_driver().setup_http_server(HTTPServerSetup("/plugins/echo", {"POST"}, echo))
        """),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    application = OpsMeshApplication(
        OpsMeshSettings(
            data_directory=tmp_path / "data",
            plugins=(name,),
            worker_enabled=False,
            max_http_body_bytes=32,
        )
    )
    await application.initialize()
    app = create_http_app(application)
    assert name not in manager.plugins
    try:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://local.test") as client,
        ):
            response = await client.post("/plugins/echo", content=b"hello")
            assert response.status_code == 200 and response.content == b"hello"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert (await client.post("/plugins/echo", content=b"x" * 33)).status_code == 413
            plugin = manager.plugins[name]
            assert plugin.module.calls == [b"hello"]
            manager.disable(name)
            assert (await client.post("/plugins/echo", content=b"denied")).status_code == 403
            assert (await client.get("/health")).status_code == 200
    finally:
        manager.plugins.pop(name, None)
        sys.modules.pop(name, None)
