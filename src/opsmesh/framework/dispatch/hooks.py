"""Register event and matcher lifecycle hooks in process-local sets."""

from typing import Any

from opsmesh.framework.dependencies import Dependent
from opsmesh.framework.dependencies.context import ContextParam
from opsmesh.framework.dependencies.params import (
    ArgParam,
    ChannelParam,
    DefaultParam,
    DependParam,
    EventParam,
    ExceptionParam,
    MatcherParam,
    StateParam,
)
from opsmesh.framework.typing import (
    T_EventPostProcessor,
    T_EventPreProcessor,
    T_RunPostProcessor,
    T_RunPreProcessor,
)

_event_preprocessors: set[Dependent[Any]] = set()
_event_postprocessors: set[Dependent[Any]] = set()
_run_preprocessors: set[Dependent[Any]] = set()
_run_postprocessors: set[Dependent[Any]] = set()

EVENT_PCS_PARAMS = (
    ContextParam,
    DependParam,
    ChannelParam,
    EventParam,
    StateParam,
    DefaultParam,
)
RUN_PREPCS_PARAMS = (
    ContextParam,
    DependParam,
    ChannelParam,
    EventParam,
    StateParam,
    ArgParam,
    MatcherParam,
    DefaultParam,
)
RUN_POSTPCS_PARAMS = (
    ContextParam,
    DependParam,
    ExceptionParam,
    ChannelParam,
    EventParam,
    StateParam,
    ArgParam,
    MatcherParam,
    DefaultParam,
)


def event_preprocessor(func: T_EventPreProcessor) -> T_EventPreProcessor:
    """Register an event preprocessor.

    The decorated callable runs before dispatching each event to matchers.
    """
    from opsmesh.framework.plugins.registry import register_hook

    return register_hook(_event_preprocessors, func, EVENT_PCS_PARAMS)


def event_postprocessor(func: T_EventPostProcessor) -> T_EventPostProcessor:
    """Register an event postprocessor.

    The decorated callable runs after dispatching each event to matchers.
    """
    from opsmesh.framework.plugins.registry import register_hook

    return register_hook(_event_postprocessors, func, EVENT_PCS_PARAMS)


def run_preprocessor(func: T_RunPreProcessor) -> T_RunPreProcessor:
    """Register a matcher preprocessor.

    The decorated callable runs before each matcher executes.
    """
    from opsmesh.framework.plugins.registry import register_hook

    return register_hook(_run_preprocessors, func, RUN_PREPCS_PARAMS)


def run_postprocessor(func: T_RunPostProcessor) -> T_RunPostProcessor:
    """Register a matcher postprocessor.

    The decorated callable runs after each matcher executes.
    """
    from opsmesh.framework.plugins.registry import register_hook

    return register_hook(_run_postprocessors, func, RUN_POSTPCS_PARAMS)
