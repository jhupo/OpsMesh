"""Extension declaration contracts: operations, hooks, events, tools and channels.

These modules are pure declarations. They never import the transport, host,
client or development layers, and they never execute business code: the
server validates declarations and runs approved code in isolated
environments.
"""

from opsmesh_sdk.extensions.channels import (
    ChannelConnectionStatus,
    InboundMessage,
    NormalizedMessage,
    OutboundMessage,
    SignatureVerifier,
)
from opsmesh_sdk.extensions.events import (
    EventEnvelope,
    EventFilter,
    EventSubscriptionNeed,
)
from opsmesh_sdk.extensions.hooks import (
    Block,
    Continue,
    HookDecision,
    HookInput,
    Patch,
)
from opsmesh_sdk.extensions.operations import (
    Operation,
    OperationResult,
    declare_operation,
)
from opsmesh_sdk.extensions.tools import ToolContent, ToolSchema, tool_schema_for_operation

__all__ = [
    "Block",
    "ChannelConnectionStatus",
    "Continue",
    "EventEnvelope",
    "EventFilter",
    "EventSubscriptionNeed",
    "HookDecision",
    "HookInput",
    "InboundMessage",
    "NormalizedMessage",
    "Operation",
    "OperationResult",
    "OutboundMessage",
    "Patch",
    "SignatureVerifier",
    "ToolContent",
    "ToolSchema",
    "declare_operation",
    "tool_schema_for_operation",
]
