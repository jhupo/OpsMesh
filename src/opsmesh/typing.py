"""本模块定义了 OpsMesh 模块中共享的一些类型。

FrontMatter:
    mdx:
        format: md
    sidebar_position: 11
    description: opsmesh.typing 模块
"""

import sys
import types
import typing as t
from typing import TYPE_CHECKING, ParamSpec, TypeAlias, TypeVar, get_args, get_origin
import typing_extensions as t_ext

if TYPE_CHECKING:
    from opsmesh.adapters import Gateway
    from opsmesh.internal.params import DependencyCache
    from opsmesh.permission import Permission

T = TypeVar("T")
P = ParamSpec("P")

T_Wrapped: TypeAlias = t.Callable[P, T]


def type_has_args(type_: type[t.Any]) -> bool:
    return isinstance(type_, (t._GenericAlias, types.GenericAlias, types.UnionType))  # type: ignore


def origin_is_union(origin: type[t.Any] | None) -> bool:
    return origin is t.Union or origin is types.UnionType


def origin_is_literal(origin: type[t.Any] | None) -> bool:
    """判断是否是 Literal 类型"""
    return origin is t.Literal


def _literal_values(type_: type[t.Any]) -> tuple[t.Any, ...]:
    return get_args(type_)


def all_literal_values(type_: type[t.Any]) -> list[t.Any]:
    """获取 Literal 类型包含的所有值"""
    if not origin_is_literal(get_origin(type_)):
        return [type_]

    return [x for value in _literal_values(type_) for x in all_literal_values(value)]


def origin_is_annotated(origin: type[t.Any] | None) -> bool:
    """判断是否是 Annotated 类型"""
    return origin is t.Annotated


NONE_TYPES = {None, types.NoneType, t.Literal[None]}  # noqa: PYI061


def is_none_type(type_: type[t.Any]) -> bool:
    """判断是否是 None 类型"""
    return type_ in NONE_TYPES


if sys.version_info < (3, 12):

    def is_type_alias_type(type_: type[t.Any]) -> bool:
        """判断是否是 TypeAliasType 类型"""
        return isinstance(type_, t_ext.TypeAliasType)

else:

    def is_type_alias_type(type_: type[t.Any]) -> bool:
        return isinstance(type_, (t.TypeAliasType, t_ext.TypeAliasType))


def evaluate_forwardref(
    ref: t.ForwardRef, globalns: dict[str, t.Any], localns: dict[str, t.Any]
) -> t.Any:
    return ref._evaluate(globalns, localns, recursive_guard=frozenset())


# state
class StateFlag:
    def __repr__(self) -> str:
        return "StateFlag()"


_STATE_FLAG = StateFlag()

T_State: TypeAlias = t.Annotated[dict[t.Any, t.Any], _STATE_FLAG]
"""事件处理状态 State 类型"""

_DependentCallable: TypeAlias = t.Callable[..., T] | t.Callable[..., t.Awaitable[T]]

# gateway connection hooks
T_ConnectHook: TypeAlias = _DependentCallable[t.Any]
"""网关登记时钩子函数"""
T_DisconnectHook: TypeAlias = _DependentCallable[t.Any]
"""网关注销时钩子函数"""

# gateway invoke hooks
T_BeforeInvokeHook: TypeAlias = t.Callable[
    ["Gateway", str, dict[str, t.Any]], t.Awaitable[t.Any]
]
"""`gateway.invoke` 前置钩子函数，参数分别为 gateway, action, params"""
T_AfterInvokeHook: TypeAlias = t.Callable[
    ["Gateway", Exception | None, str, dict[str, t.Any], t.Any], t.Awaitable[t.Any]
]
"""`gateway.invoke` 后置钩子，参数为 gateway, exception, action, params, result"""

# event hooks
T_EventPreProcessor: TypeAlias = _DependentCallable[t.Any]
"""事件预处理函数 EventPreProcessor 类型"""
T_EventPostProcessor: TypeAlias = _DependentCallable[t.Any]
"""事件后处理函数 EventPostProcessor 类型"""

# matcher run hooks
T_RunPreProcessor: TypeAlias = _DependentCallable[t.Any]
"""事件响应器运行前预处理函数 RunPreProcessor 类型"""
T_RunPostProcessor: TypeAlias = _DependentCallable[t.Any]
"""事件响应器运行后后处理函数 RunPostProcessor 类型"""

# rule, permission
T_RuleChecker: TypeAlias = _DependentCallable[bool]
"""RuleChecker 即判断是否响应事件的处理函数。"""
T_PermissionChecker: TypeAlias = _DependentCallable[bool]
"""PermissionChecker 即判断事件是否满足权限的处理函数。"""

T_Handler: TypeAlias = _DependentCallable[t.Any]
"""Handler 处理函数。"""
T_TypeUpdater: TypeAlias = _DependentCallable[str]
"""TypeUpdater 在 Matcher.pause, Matcher.reject 时被运行，用于更新响应的事件类型。"""
T_PermissionUpdater: TypeAlias = _DependentCallable["Permission"]
"""PermissionUpdater 在 Matcher.pause/reject 时运行，用于更新会话对象权限。"""
T_DependencyCache: TypeAlias = dict[_DependentCallable[t.Any], "DependencyCache"]
"""依赖缓存, 用于存储依赖函数的返回值"""
