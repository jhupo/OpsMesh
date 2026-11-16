"""Group predicates composed with the framework's native Rule."""

from dataclasses import dataclass
from typing import Literal

from opsmesh.adapters import Channel, Event
from opsmesh.drivers import get_driver
from opsmesh.plugins import get_current_plugin, get_dependency_config
from opsmesh.rules import Rule

from .config import ChannelConnections


@dataclass(frozen=True)
class GroupRule:
    """Match verified robot messages within their declaring plugin."""

    groups: frozenset[str]
    owner_id: str
    connection: str | None
    protocol: Literal["qq", "dingtalk"] | None

    async def __call__(self, channel: Channel, event: Event) -> bool:
        """Match verified group identity within the declaring plugin."""
        protocol = channel.adapter.get_name()
        if protocol not in {"qq", "dingtalk"}:
            return False
        if self.protocol is not None and self.protocol != protocol:
            return False
        if channel.owner_id != self.owner_id:
            return False
        if (
            self.connection is not None
            and channel.connection_name != self.connection
        ):
            return False
        if protocol == "qq":
            from .qq.events import QQMessageEvent

            group = (
                event.group_id if isinstance(event, QQMessageEvent) else None
            )
        else:
            from .dingtalk.events import DingTalkMessageEvent

            group = (
                event.conversation_id
                if isinstance(event, DingTalkMessageEvent)
                and event.conversation_type == "2"
                else None
            )
        if group is None:
            return False
        return event.connection_id == channel.self_id and group in self.groups


def in_group(
    *groups: str,
    connection: str | None = None,
    protocol: Literal["qq", "dingtalk"] | None = None,
) -> Rule:
    """Match platform group IDs without selecting or constructing an Agent.

    Args:
        *groups: QQ group openids or DingTalk conversation IDs.
        connection: Optional connection name within the current plugin.
        protocol: Required when a selected name occurs in both protocols.

    Returns:
        A native Rule usable by message and command matchers.
    """
    if not groups or any(
        not isinstance(group, str) or not group for group in groups
    ):
        raise ValueError("At least one nonempty group ID is required")
    if protocol not in (None, "qq", "dingtalk"):
        raise ValueError("Unsupported group protocol")
    if connection is not None and not connection:
        raise ValueError("Connection name must be nonempty")
    owner = get_current_plugin()
    if owner is None:
        raise RuntimeError("Group rules must be declared by a loaded plugin")

    def validate() -> None:
        settings = get_dependency_config(ChannelConnections, owner)
        if (
            protocol is None
            and connection is not None
            and connection in settings.qq
            and connection in settings.dingtalk
        ):
            raise ValueError(
                f"Ambiguous connection {connection!r}; specify its protocol"
            )

    get_driver().on_startup(validate)
    return Rule(GroupRule(frozenset(groups), owner.id_, connection, protocol))
