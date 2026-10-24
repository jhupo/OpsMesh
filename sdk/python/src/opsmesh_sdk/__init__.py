"""Public extension authoring interface; no business API client or server imports."""

from opsmesh_sdk.hooks import AgentHookEvent, HookPoint
from opsmesh_sdk.identity import AuthorizationRequest, InvocationSource, Principal
from opsmesh_sdk.manifest import PluginManifest
from opsmesh_sdk.messages import MessageFilterResult
from opsmesh_sdk.middleware import ModelMiddleware, ModelRequest
from opsmesh_sdk.registration import (
    PluginContext,
    get_invocation,
    get_plugin_credential,
    get_tool_config,
)

__version__ = "0.1.3"
__all__ = [
    "PluginContext",
    "PluginManifest",
    "Principal",
    "InvocationSource",
    "AuthorizationRequest",
    "AgentHookEvent",
    "HookPoint",
    "ModelMiddleware",
    "ModelRequest",
    "MessageFilterResult",
    "get_invocation",
    "get_tool_config",
    "get_plugin_credential",
    "__version__",
]
