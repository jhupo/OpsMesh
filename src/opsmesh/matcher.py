"""本模块实现事件响应器的创建与运行，并提供一些快捷方法来帮助用户更好的处理多轮消息交互。

FrontMatter:
    mdx:
        format: md
    sidebar_position: 3
    description: opsmesh.matcher 模块
"""

from opsmesh.internal.matcher import DEFAULT_PROVIDER_CLASS as DEFAULT_PROVIDER_CLASS
from opsmesh.internal.matcher import Matcher as Matcher
from opsmesh.internal.matcher import MatcherManager as MatcherManager
from opsmesh.internal.matcher import MatcherProvider as MatcherProvider
from opsmesh.internal.matcher import MatcherSource as MatcherSource
from opsmesh.internal.matcher import current_event as current_event
from opsmesh.internal.matcher import current_gateway as current_gateway
from opsmesh.internal.matcher import current_handler as current_handler
from opsmesh.internal.matcher import current_matcher as current_matcher
from opsmesh.internal.matcher import matchers as matchers

__autodoc__ = {
    "Matcher": True,
    "matchers": True,
    "MatcherManager": True,
    "MatcherProvider": True,
    "DEFAULT_PROVIDER_CLASS": True,
}
