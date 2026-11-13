"""Verify operation rejection and observation error boundaries."""

import asyncio

import pytest

from opsmesh.adapters import Channel
from opsmesh.exceptions import MockCallException


@pytest.mark.anyio
async def test_rejection_wins_over_mock_without_calling_adapter(
    conversation, monkeypatch
):
    channel, _ = conversation
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
async def test_observer_failure_preserves_success(conversation, monkeypatch):
    channel, _ = conversation

    async def operation(*args, **kwargs):
        return "sent"

    async def observe(*args):
        raise RuntimeError("Observer failed")

    monkeypatch.setattr(Channel, "_calling_hook", set())
    monkeypatch.setattr(Channel, "_called_hook", {observe})
    channel.adapter._call = operation
    assert await channel.call("send_message") == "sent"
