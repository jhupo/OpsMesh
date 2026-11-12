"""Shared typing definitions used by the framework.

Type annotations follow Python's typing conventions;
see PEP 484 at https://peps.python.org/pep-0484/,
PEP 526 at https://peps.python.org/pep-0526/, and
[`typing`](https://docs.python.org/3/library/typing.html).
"""

import sys
import types
import typing as t
from typing import (
    TYPE_CHECKING,
    ParamSpec,
    TypeAlias,
    TypeVar,
    get_args,
    get_origin,
)

import typing_extensions as t_ext

if TYPE_CHECKING:
    from opsmesh.framework.adapters import Channel
    from opsmesh.framework.dependencies.params import DependencyCache
    from opsmesh.framework.permissions import Permission

T = TypeVar("T")
P = ParamSpec("P")

T_Wrapped: TypeAlias = t.Callable[P, T]


def type_has_args(type_: type[t.Any]) -> bool:
    """Return whether an annotation carries generic type arguments."""
    return isinstance(
        type_, (t._GenericAlias, types.GenericAlias, types.UnionType)
    )  # type: ignore


def origin_is_union(origin: type[t.Any] | None) -> bool:
    """Recognize both typing.Union and native union origins."""
    return origin is t.Union or origin is types.UnionType


def origin_is_literal(origin: type[t.Any] | None) -> bool:
    """Check whether an annotation is a Literal."""
    return origin is t.Literal


def _literal_values(type_: type[t.Any]) -> tuple[t.Any, ...]:
    return get_args(type_)


def all_literal_values(type_: type[t.Any]) -> list[t.Any]:
    """Return all values represented by a Literal annotation."""
    if not origin_is_literal(get_origin(type_)):
        return [type_]

    return [
        x for value in _literal_values(type_) for x in all_literal_values(value)
    ]


def origin_is_annotated(origin: type[t.Any] | None) -> bool:
    """Check whether an annotation is Annotated."""
    return origin is t.Annotated


NONE_TYPES = {None, types.NoneType, t.Literal[None]}  # noqa: PYI061


def is_none_type(type_: type[t.Any]) -> bool:
    """Check whether an annotation represents None."""
    return type_ in NONE_TYPES


if sys.version_info < (3, 12):

    def is_type_alias_type(type_: type[t.Any]) -> bool:
        """Check whether an annotation is a TypeAliasType."""
        return isinstance(type_, t_ext.TypeAliasType)

else:

    def is_type_alias_type(type_: type[t.Any]) -> bool:
        """Return whether an object is a PEP 695 type alias."""
        return isinstance(type_, (t.TypeAliasType, t_ext.TypeAliasType))


def evaluate_forwardref(
    ref: t.ForwardRef, globalns: dict[str, t.Any], localns: dict[str, t.Any]
) -> t.Any:
    # Python 3.13/3.12.4+ made `recursive_guard` a kwarg,
    # so name it explicitly to avoid:
    # TypeError: ForwardRef._evaluate()
    # missing 1 required keyword-only argument: 'recursive_guard'
    """Resolve a forward reference across supported Python versions."""
    return ref._evaluate(globalns, localns, recursive_guard=frozenset())


# state
# use annotated flag to avoid ForwardRef recreate generic type (py >= 3.11)
class StateFlag:
    """Marker used to identify injected matcher state annotations."""

    def __repr__(self) -> str:
        return "StateFlag()"


_STATE_FLAG = StateFlag()

T_State: TypeAlias = t.Annotated[dict[t.Any, t.Any], _STATE_FLAG]
"""The mutable event-processing state mapping."""

_DependentCallable: TypeAlias = (
    t.Callable[..., T] | t.Callable[..., t.Awaitable[T]]
)

# driver hooks
T_ConnectionHook: TypeAlias = _DependentCallable[t.Any]
"""A hook called when a channel connects.

Supported dependency descriptors:

- DependParam: A nested dependency.
- ChannelParam: The current channel instance.
- DefaultParam: A declared default value.
"""
T_DisconnectionHook: TypeAlias = _DependentCallable[t.Any]
"""A hook called when a channel disconnects.

Supported dependency descriptors:

- DependParam: A nested dependency.
- ChannelParam: The current channel instance.
- DefaultParam: A declared default value.
"""

# operation hooks
T_CallingHook: TypeAlias = t.Callable[
    ["Channel", str, dict[str, t.Any]], t.Awaitable[t.Any]
]
"""A hook executed before channel.call."""
T_CalledHook: TypeAlias = t.Callable[
    ["Channel", Exception | None, str, dict[str, t.Any], t.Any],
    t.Awaitable[t.Any],
]
"""A hook executed after channel.call.

Arguments are channel, exception, operation, data, and result, in order.
"""

# event hooks
T_EventPreProcessor: TypeAlias = _DependentCallable[t.Any]
"""An event preprocessor callable.

Supported dependency descriptors:

- DependParam: A nested dependency.
- ChannelParam: The current channel instance.
- EventParam: The current event instance.
- StateParam: The current state mapping.
- DefaultParam: A declared default value.
"""
T_EventPostProcessor: TypeAlias = _DependentCallable[t.Any]
"""An event postprocessor callable.

Supported dependency descriptors:

- DependParam: A nested dependency.
- ChannelParam: The current channel instance.
- EventParam: The current event instance.
- StateParam: The current state mapping.
- DefaultParam: A declared default value.
"""

# matcher run hooks
T_RunPreProcessor: TypeAlias = _DependentCallable[t.Any]
"""A preprocessor invoked before matcher execution.

Supported dependency descriptors:

- DependParam: A nested dependency.
- ChannelParam: The current channel instance.
- EventParam: The current event instance.
- StateParam: The current state mapping.
- MatcherParam: The current matcher instance.
- DefaultParam: A declared default value.
"""
T_RunPostProcessor: TypeAlias = _DependentCallable[t.Any]
"""A postprocessor invoked after matcher execution.

Supported dependency descriptors:

- DependParam: A nested dependency.
- ChannelParam: The current channel instance.
- EventParam: The current event instance.
- StateParam: The current state mapping.
- MatcherParam: The current matcher instance.
- ExceptionParam: The execution exception, or None.
- DefaultParam: A declared default value.
"""

# rule, permission
T_RuleChecker: TypeAlias = _DependentCallable[bool]
"""A rule checker deciding whether an event matches.

Supported dependency descriptors:

- DependParam: A nested dependency.
- ChannelParam: The current channel instance.
- EventParam: The current event instance.
- StateParam: The current state mapping.
- DefaultParam: A declared default value.
"""
T_PermissionChecker: TypeAlias = _DependentCallable[bool]
"""A permission checker deciding whether an event is allowed.

Supported dependency descriptors:

- DependParam: A nested dependency.
- ChannelParam: The current channel instance.
- EventParam: The current event instance.
- DefaultParam: A declared default value.
"""

T_Handler: TypeAlias = _DependentCallable[t.Any]
"""An event handler callable."""
T_TypeUpdater: TypeAlias = _DependentCallable[str]
"""A callback updating the event type after Matcher.pause or Matcher.reject.

The default continuation event type is message.

Supported dependency descriptors:

- DependParam: A nested dependency.
- ChannelParam: The current channel instance.
- EventParam: The current event instance.
- StateParam: The current state mapping.
- MatcherParam: The current matcher instance.
- DefaultParam: A declared default value.
"""
T_PermissionUpdater: TypeAlias = _DependentCallable["Permission"]
"""A callback updating permissions after Matcher.pause or Matcher.reject.

By default, continuations are restricted to the current conversation.

Supported dependency descriptors:

- DependParam: A nested dependency.
- ChannelParam: The current channel instance.
- EventParam: The current event instance.
- StateParam: The current state mapping.
- MatcherParam: The current matcher instance.
- DefaultParam: A declared default value.
"""
T_DependencyCache: TypeAlias = dict[
    _DependentCallable[t.Any], "DependencyCache"
]
"""A cache mapping dependency callables to their resolution results."""
