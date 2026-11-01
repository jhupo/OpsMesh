"""Reviewed delivery cannot feed unreviewed model output into shared memory."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from opsmesh.application.conversations.gateway import ConversationGateway
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError


@pytest.mark.parametrize("automatic", [False, True])
async def test_gateway_rejects_automatic_capture_before_run_creation(tmp_path, automatic):
    spec = AgentSpecification(
        id="agent", name="Agent", memory_namespace_id="memory", memory_generate=automatic
    )
    bots = SimpleNamespace(
        get_account=AsyncMock(
            return_value=SimpleNamespace(
                enabled=True,
                unlinked_principal_id="owner",
                unlinked_channels=("group",),
            )
        ),
        get_route=AsyncMock(
            return_value=SimpleNamespace(dispatch_mode="managed", agent_revision="agent@1")
        ),
    )
    agents = SimpleNamespace(get_revision=AsyncMock(return_value=spec))
    gateway = ConversationGateway(bot_id="bot", channel_id="group", principal_id="owner")
    if automatic:
        with pytest.raises(OpsMeshError) as error:
            await gateway.resolve(bots, agents, tmp_path)
        assert error.value.code == "CONFIG_INVALID"
    else:
        options = await gateway.resolve(bots, agents, tmp_path)
        assert options.bindings[0].memory_namespace_id == "memory"
