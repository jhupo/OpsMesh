"""本模块定义了驱动适配器基类。

各驱动请继承以下基类。

FrontMatter:
    mdx:
        format: md
    sidebar_position: 0
    description: opsmesh.drivers 模块
"""

from opsmesh.internal.driver import DEFAULT_TIMEOUT as DEFAULT_TIMEOUT
from opsmesh.internal.driver import URL as URL
from opsmesh.internal.driver import ASGIMixin as ASGIMixin
from opsmesh.internal.driver import Cookies as Cookies
from opsmesh.internal.driver import Driver as Driver
from opsmesh.internal.driver import ForwardDriver as ForwardDriver
from opsmesh.internal.driver import ForwardMixin as ForwardMixin
from opsmesh.internal.driver import HTTPClientMixin as HTTPClientMixin
from opsmesh.internal.driver import HTTPClientSession as HTTPClientSession
from opsmesh.internal.driver import HTTPServerSetup as HTTPServerSetup
from opsmesh.internal.driver import HTTPVersion as HTTPVersion
from opsmesh.internal.driver import Mixin as Mixin
from opsmesh.internal.driver import Request as Request
from opsmesh.internal.driver import Response as Response
from opsmesh.internal.driver import ReverseDriver as ReverseDriver
from opsmesh.internal.driver import ReverseMixin as ReverseMixin
from opsmesh.internal.driver import Timeout as Timeout
from opsmesh.internal.driver import WebSocket as WebSocket
from opsmesh.internal.driver import WebSocketClientMixin as WebSocketClientMixin
from opsmesh.internal.driver import WebSocketServerSetup as WebSocketServerSetup
from opsmesh.internal.driver import combine_driver as combine_driver

__autodoc__ = {
    "DEFAULT_TIMEOUT": True,
    "URL": True,
    "Cookies": True,
    "Request": True,
    "Response": True,
    "Timeout": True,
    "WebSocket": True,
    "HTTPVersion": True,
    "Driver": True,
    "Mixin": True,
    "ForwardMixin": True,
    "ForwardDriver": True,
    "HTTPClientMixin": True,
    "WebSocketClientMixin": True,
    "ReverseMixin": True,
    "ReverseDriver": True,
    "ASGIMixin": True,
    "combine_driver": True,
    "HTTPServerSetup": True,
    "WebSocketServerSetup": True,
}
