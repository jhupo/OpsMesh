"""Run against installed adapter wheels; no account or external service required."""
import asyncio
import json
import os
import subprocess
import sys

import pytest

@pytest.mark.parametrize("name,other", [("qq", "dingtalk"), ("dingtalk", "qq")])
def test_independent_adapter(name, other):
    subprocess.run([sys.executable, "-c", '''
import sys
import opsmesh
opsmesh.init(driver="~none")
name, other = sys.argv[1:]
p = opsmesh.load_plugin(name)
assert p and p.id_ == name and p.metadata.type == "adapter"
assert not opsmesh.get_adapters()
p.manager.enable_plugin(p)
opsmesh.get_driver().run = lambda: None
opsmesh.run()
assert opsmesh.get_adapter(name).get_name() == name
assert other not in sys.modules
''', name, other], check=True)


def test_callback_reaches_framework_and_deduplicates(monkeypatch):
    from dingtalk.adapter import Adapter
    from dingtalk.config import Settings
    from dingtalk import gateway as module
    from opsmesh.config import Config, Env
    from opsmesh.drivers.none import Driver
    from dingtalk_stream import AckMessage

    adapter = Adapter(Driver(Env(), Config()), settings=Settings(accounts=[{"client_id":"account", "client_secret_ref":"TEST_UNUSED"}]))
    gateway = adapter.accounts[0]
    received = []
    async def handle(origin, event):
        received.append((origin.id, event.get_plaintext()))
    monkeypatch.setattr(module, "handle_event", handle)
    async def run():
        adapter.gateways[gateway.id] = gateway
        data = {"msgtype":"text", "text":{"content":"hello"}, "msgId":"1", "conversationId":"abc", "senderId":"user"}
        for status in ("ok", "duplicate"):
            assert await adapter.accept_callback(gateway, data) == (AckMessage.STATUS_OK, status)
        task = asyncio.create_task(gateway.worker())
        try:
            await asyncio.wait_for(gateway.queue.join(), 3)
            assert received == [("dingtalk:account", "hello")]
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize("case", ["ok", "duplicate", "missing_secret", "disabled_provider"])
def test_consumers_declare_qq_and_dingtalk_connections(tmp_path, case):
    for index in (1, 2):
        plugin = tmp_path / f"consumer_{index}"
        plugin.mkdir()
        (plugin / "__init__.py").write_text('''
from pydantic import BaseModel
from opsmesh import get_plugin_config, on_message
from opsmesh.adapters import Gateway
from opsmesh.message import event_preprocessor, event_postprocessor, run_preprocessor, run_postprocessor
from opsmesh.plugin import PluginMetadata
class Config(BaseModel):
    model: str
__plugin_meta__ = PluginMetadata(name=__name__, description="", usage="", config=Config, config_file="settings.json")
def model():
    return get_plugin_config(Config).model

seen = []

@event_preprocessor
async def before_event(gateway: Gateway):
    seen.append(("before_event", gateway.id))

@event_postprocessor
async def after_event(gateway: Gateway):
    seen.append(("after_event", gateway.id))

@run_preprocessor
async def before_run(gateway: Gateway):
    seen.append(("before_run", gateway.id))

@run_postprocessor
async def after_run(gateway: Gateway):
    seen.append(("after_run", gateway.id))

@Gateway.on_calling
async def before_api(gateway, api, data):
    seen.append(("before_api", gateway.id))

@Gateway.on_called
async def after_api(gateway, exception, api, data, result):
    seen.append(("after_api", gateway.id))

async def rule(gateway: Gateway):
    seen.append(("rule", gateway.id))
    return True

@on_message(rule=rule, block=False).handle()
async def handler(gateway: Gateway):
    seen.append(("handler", gateway.id))
    await gateway.call_api("test")
''', encoding="utf-8")
        dingtalk_id = "app1" if case == "duplicate" else f"app{index}"
        ref = "DINGTALK_NOT_SET" if case == "missing_secret" else "DINGTALK_TEST_SECRET"
        (plugin / "settings.json").write_text(json.dumps({
            "config": {"model": f"model{index}"},
            "dependencies": {
                "dingtalk": {"client_id": dingtalk_id, "client_secret_ref": ref},
                "qq": {"app_id": f"123{index}", "secret_ref": "QQ_TEST_SECRET"},
            },
        }), encoding="utf-8")
    env = {**os.environ, "QQ_TEST_SECRET": "fake", "DINGTALK_TEST_SECRET": "fake"}
    env.pop("DINGTALK_NOT_SET", None)
    subprocess.run([sys.executable, "-c", '''
import asyncio
import sys
import opsmesh
from opsmesh.adapters import Event
from opsmesh.message import handle_event
from types import SimpleNamespace
case = sys.argv[1]
opsmesh.init(driver="~aiohttp", dingtalk={"accounts": [
    {"client_id": "shared", "client_secret_ref": "DINGTALK_TEST_SECRET"},
]})
for name in ("consumer_1", "consumer_2", "qq", "dingtalk"):
    p = opsmesh.load_plugin(name)
    assert p is not None, name
    if not (case == "disabled_provider" and name == "dingtalk"):
        p.manager.enable_plugin(p)
assert not opsmesh.get_adapters()
started = []
opsmesh.get_driver().run = lambda: started.append(True)
try:
    opsmesh.run()
except ValueError as exc:
    error = {
        "duplicate": "consumer_2.dependencies.dingtalk: duplicate DingTalk client_id",
        "missing_secret": "consumer_1.dependencies.dingtalk: Missing DingTalk secret",
        "disabled_provider": "consumer_1.dependencies.dingtalk: provider is not loaded and enabled",
    }[case]
    assert error in str(exc), str(exc)
    assert not started and not opsmesh.get_adapters()
else:
    assert case == "ok" and started == [True]
    assert set(opsmesh.get_adapters()) == {"dingtalk", "qq"}
    ding = opsmesh.get_adapter("dingtalk")
    qq = opsmesh.get_adapter("qq")
    for index in (1, 2):
        owner = f"consumer_{index}"
        assert opsmesh.get_plugin(owner).module.model() == f"model{index}"
        assert ding.connections[owner].id == f"dingtalk:app{index}"
        assert ding.connections[owner].owner_plugin_id == owner
        assert qq.connections[owner].id == f"qq:123{index}"
        assert qq.connections[owner].owner_plugin_id == owner

    class Message(Event):
        def get_type(self): return "message"
        def get_event_name(self): return "message"
        def get_event_description(self): return "hello"
        def get_session_id(self): return "session"
        def get_user_id(self): return "user"
        def get_message(self): return [SimpleNamespace(is_text=lambda: False)]
        def is_tome(self): return True

    async def fake_api(gateway, api):
        return None

    ding._call_api = fake_api
    qq._call_api = fake_api
    c = opsmesh.get_plugin("consumer_1")
    d = opsmesh.get_plugin("consumer_2")
    a, b = ding.connections[c.id_], qq.connections[d.id_]
    shared = next(g for g in ding.accounts if g.id == "dingtalk:shared")
    assert shared.owner_plugin_id is None

    async def check_routing():
        steps = [
            "before_event", "rule", "before_run", "handler", "before_api",
            "after_api", "after_run", "after_event",
        ]
        await handle_event(a, Message())
        assert c.module.seen == [(step, a.id) for step in steps]
        assert not d.module.seen
        c.module.seen.clear()
        await handle_event(b, Message())
        assert not c.module.seen
        assert d.module.seen == [(step, b.id) for step in steps]
        d.module.seen.clear()
        await handle_event(shared, Message())
        for plugin in (c, d):
            assert ("handler", shared.id) in plugin.module.seen
            plugin.module.seen.clear()
        c.manager.disable_plugin(c)
        await handle_event(a, Message())
        assert not c.module.seen and not d.module.seen

    asyncio.run(check_routing())
''', case], check=True, cwd=tmp_path, env=env, timeout=20)
