"""Standalone QQ protocol plugin, ported from nonebot/adapter-qq."""

from opsmesh.plugin import PluginMetadata

from .adapter import Adapter as Adapter
from .config import Account, Config
from .event import *  # noqa: F403
from .exception import (
    ActionFailed as ActionFailed,
)
from .exception import (
    ApiNotAvailable as ApiNotAvailable,
)
from .exception import (
    AuditException as AuditException,
)
from .exception import (
    NetworkError as NetworkError,
)
from .exception import (
    NoLogException as NoLogException,
)
from .exception import (
    QQAdapterException as QQAdapterException,
)
from .exception import (
    RateLimitException as RateLimitException,
)
from .exception import (
    UnauthorizedException as UnauthorizedException,
)
from .gateway import Gateway as Gateway
from .message import Message as Message
from .message import MessageSegment as MessageSegment
from .permission import *  # noqa: F403

__plugin_meta__ = PluginMetadata(
    name="QQ",
    description="QQ Bot WebSocket and Webhook adapter",
    usage="Declare a QQ connection, or configure plugin_settings.qq.accounts.",
    type="adapter",
    config=Config,
    adapter=Adapter,
    dependency_config=Account,
)
