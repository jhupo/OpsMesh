"""Group predicates composed with the framework's native Rule."""

from dataclasses import dataclass
from typing import Literal

from opsmesh.adapters import Channel, Event
from opsmesh.plugins import get_current_plugin
from opsmesh.rules import Rule

from .events import MessageEvent

ChannelProtocol = Literal["api", "qq", "dingtalk"]


@dataclass(frozen=True)
class GroupRule:
    """Match group messages within their declaring plugin and connection."""

    groups: frozenset[str]
    owner_id: str
    connection: str | None
    protocol: ChannelProtocol | None

    async def __call__(self, channel: Channel, event: Event) -> bool:
        """Match a normalized group identifier within the declaring plugin."""
        if self.protocol is not None and self.protocol != channel.type:
            return False
        if channel.owner_id != self.owner_id:
            return False
        if (
            self.connection is not None
            and channel.connection_name != self.connection
        ):
            return False
        if not isinstance(event, MessageEvent):
            return False
        if event.conversation_type != "group" or event.group_id is None:
            return False
        return (
            event.connection_id == channel.self_id
            and event.group_id in self.groups
        )


def in_group(
    *groups: str,
    connection: str | None = None,
    protocol: ChannelProtocol | None = None,
) -> Rule:
    """Match normalized group IDs without selecting or constructing an Agent.

    Args:
        *groups: QQ group openids or DingTalk conversation IDs.
        connection: Optional connection name within the current plugin.
        protocol: Optional protocol selector when a connection name is shared.

    Returns:
        A native Rule usable by message and command matchers.
    """
    if not groups or any(
        not isinstance(group, str) or not group for group in groups
    ):
        raise ValueError("At least one nonempty group ID is required")
    if protocol not in (None, "api", "qq", "dingtalk"):
        raise ValueError("Unsupported group protocol")
    if connection is not None and not connection:
        raise ValueError("Connection name must be nonempty")
    owner = get_current_plugin()
    if owner is None:
        raise RuntimeError("Group rules must be declared by a loaded plugin")

    return Rule(GroupRule(frozenset(groups), owner.id_, connection, protocol))
