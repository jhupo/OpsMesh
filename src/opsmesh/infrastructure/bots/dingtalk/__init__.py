"""DingTalk application robot over authenticated Stream and OpenAPI transports."""

from opsmesh.infrastructure.bots.dingtalk.adapter import DingTalkAdapter
from opsmesh.infrastructure.bots.dingtalk.bot import DingTalkBot
from opsmesh.infrastructure.bots.dingtalk.configuration import DingTalkConfiguration
from opsmesh.infrastructure.bots.dingtalk.event import DingTalkMessageEvent

__all__ = ["DingTalkAdapter", "DingTalkBot", "DingTalkConfiguration", "DingTalkMessageEvent"]
