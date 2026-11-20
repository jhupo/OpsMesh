"""Standalone DingTalk Stream adapter."""
from opsmesh.plugin import PluginMetadata
from .adapter import Adapter
from .config import Config
from .gateway import Gateway
from .protocol.config import DingTalkConnectionSettings
from .protocol.events import DingTalkMessageEvent, DingTalkNoticeEvent

__plugin_meta__ = PluginMetadata(
    name="DingTalk",
    description="DingTalk Stream adapter",
    usage="Declare a DingTalk connection, or configure dingtalk.accounts.",
    type="adapter",
    config=Config,
    adapter=Adapter,
    dependency_config=DingTalkConnectionSettings,
)

__all__ = ["Adapter", "Gateway", "DingTalkMessageEvent", "DingTalkNoticeEvent"]
