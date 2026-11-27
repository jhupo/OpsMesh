"""本模块定义了协议适配基类，各协议请继承以下基类。

使用 {ref}`opsmesh.drivers.Driver.register_adapter` 注册适配器。

FrontMatter:
    mdx:
        format: md
    sidebar_position: 0
    description: opsmesh.adapters 模块
"""

from pkgutil import extend_path

from opsmesh.internal.adapter import Adapter as Adapter
from opsmesh.internal.adapter import Event as Event
from opsmesh.internal.adapter import Gateway as Gateway
from opsmesh.internal.adapter import Message as Message
from opsmesh.internal.adapter import MessageSegment as MessageSegment
from opsmesh.internal.adapter import MessageTemplate as MessageTemplate

__path__ = extend_path(__path__, __name__)

__autodoc__ = {
    "Gateway": True,
    "Event": True,
    "Adapter": True,
    "Message": True,
    "Message.__getitem__": True,
    "Message.__contains__": True,
    "Message._construct": True,
    "MessageSegment": True,
    "MessageSegment.__str__": True,
    "MessageSegment.__add__": True,
    "MessageTemplate": True,
}
