"""OpsMesh native messaging gateway."""

from .channels.manager import Gateway
from .channels.registry import ChannelRegistry
from .contracts import ChannelDefinition, ChannelTarget, GatewayTurn

__all__ = ["ChannelDefinition", "GatewayTurn", "ChannelTarget", "Gateway", "ChannelRegistry"]
