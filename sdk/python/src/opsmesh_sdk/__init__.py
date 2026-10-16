"""OpsMesh SDK: independent client, extension and host contracts.

The SDK never imports the server. Business API groups are generated from the
platform OpenAPI and arrive with their server steps; the stable contracts in
this package (values, errors, manifest, transport, extensions, host) are
hand-written and shared by external clients and extensions alike.
"""

from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.context import InvocationContext, InvocationSubject, PlatformFacade
from opsmesh_sdk.errors import (
    AuthenticationError,
    AuthorizationError,
    CancelledError,
    ConflictError,
    NotFoundError,
    OpsMeshError,
    RateLimitError,
    SecurityError,
    TimeoutError,
    TransportError,
    UnknownEffectError,
    ValidationError,
)
from opsmesh_sdk.manifest import (
    DataPolicy,
    EffectKind,
    EventExport,
    HookExport,
    HookFailurePolicy,
    HookStage,
    OperationExport,
    PackageManifest,
    Permission,
    validate_manifest,
)
from opsmesh_sdk.transport.contracts import (
    CancellationScope,
    StreamChunk,
    Transport,
    TransportRequest,
    TransportResponse,
)
from opsmesh_sdk.transport.host import DelegationIdentity, HostTransport
from opsmesh_sdk.transport.http import HttpTransport
from opsmesh_sdk.transport.streams import EventStreamReader
from opsmesh_sdk.values import (
    ContentBlock,
    Cursor,
    FlowRunRef,
    IdempotencyKey,
    Page,
    ResourceRef,
    ScopeRef,
    TraceContext,
    Usage,
)

__version__ = "0.1.0rc23"

__all__ = [
    "AuthenticationError",
    "AuthorizationError",
    "CancelledError",
    "CancellationScope",
    "ContentBlock",
    "ConflictError",
    "Cursor",
    "DataPolicy",
    "DelegationIdentity",
    "EffectKind",
    "EventExport",
    "EventStreamReader",
    "FlowRunRef",
    "HookExport",
    "HookFailurePolicy",
    "HookStage",
    "HostTransport",
    "HttpTransport",
    "IdempotencyKey",
    "InvocationContext",
    "InvocationSubject",
    "NotFoundError",
    "OpsMeshError",
    "OperationExport",
    "PackageManifest",
    "Page",
    "Permission",
    "PlatformClient",
    "PlatformFacade",
    "RateLimitError",
    "ResourceRef",
    "ScopeRef",
    "SecurityError",
    "StreamChunk",
    "TimeoutError",
    "TraceContext",
    "Transport",
    "TransportError",
    "TransportRequest",
    "TransportResponse",
    "UnknownEffectError",
    "Usage",
    "ValidationError",
    "__version__",
    "validate_manifest",
]
