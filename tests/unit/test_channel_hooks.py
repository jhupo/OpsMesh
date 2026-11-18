"""Verify operation rejection and observation error boundaries."""

import asyncio
from types import ModuleType

import pytest

from opsmesh.config import Config, Env
from opsmesh.drivers import Driver
from opsmesh.plugins.channels import Channel, MockCallException
from opsmesh.plugins.channels.api import APIAdapter, APIConnectionSettings
from opsmesh.plugins.manager import PluginManager
from opsmesh.plugins.model import Plugin, PluginStatus
from opsmesh.plugins.registry import plugin_scope


@pytest.fixture
async def channel():
    owner = Plugin(
        "support",
        ModuleType("support"),
        "support",
        PluginManager(),
        status=PluginStatus.READY,
    )
    adapter = APIAdapter(Driver(Env(), Config()))
    with plugin_scope(owner):
        endpoint = adapter.declare(APIConnectionSettings(enabled=False))
    try:
        yield endpoint
    finally:
        await endpoint.close()


@pytest.mark.anyio
async def test_rejection_wins_over_mock_without_calling_adapter(
    channel, monkeypatch
):
    called = []

    async def operation(*args, **kwargs):
        called.append(True)

    async def reject(*args):
        await asyncio.sleep(0)
        raise PermissionError("Denied")

    async def replace(*args):
        raise MockCallException("replacement")

    monkeypatch.setattr(Channel, "_calling_hook", {reject, replace})
    monkeypatch.setattr(Channel, "_called_hook", set())
    channel.adapter._call = operation
    with pytest.raises(PermissionError, match="Denied"):
        await channel.call("remove_member")
    assert called == []


@pytest.mark.anyio
async def test_observer_failure_preserves_success(channel, monkeypatch):

    async def operation(*args, **kwargs):
        return "sent"

    async def observe(*args):
        raise RuntimeError("Observer failed")

    monkeypatch.setattr(Channel, "_calling_hook", set())
    monkeypatch.setattr(Channel, "_called_hook", {observe})
    channel.adapter._call = operation
    assert await channel.call("send_message") == "sent"
