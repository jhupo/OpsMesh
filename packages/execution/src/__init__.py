"""Native execution, framework callback adaptation, and tracing."""

from opsmesh.plugins import PluginMetadata

from . import _lifecycle as _lifecycle
from ._tracing import TraceContext
from .callbacks import (
    before_run,
    on_agent_end,
    on_agent_start,
    on_handoff,
    on_llm_end,
    on_llm_start,
    on_run_end,
    on_tool_end,
    on_tool_start,
)
from .config import (
    LangfuseSettings,
    Settings,
    TracingSettings,
)
from .errors import ExecutionClosed, ExecutionError, TracingConfigurationError
from .runner import run, stream

__plugin_meta__ = PluginMetadata(
    name="execution",
    description="Native Runner calls with framework callbacks and tracing",
    usage="Require execution, then call run or consume stream in its scope.",
    type="library",
    config=Settings,
)

__all__ = [
    "Settings",
    "LangfuseSettings",
    "TracingSettings",
    "ExecutionClosed",
    "ExecutionError",
    "TracingConfigurationError",
    "TraceContext",
    "run",
    "stream",
    "before_run",
    "on_run_end",
    "on_agent_start",
    "on_agent_end",
    "on_handoff",
    "on_llm_start",
    "on_llm_end",
    "on_tool_start",
    "on_tool_end",
]
