"""Isolated package entry point; never imported by the platform Worker."""

import importlib.metadata
from typing import Any

from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.context import InvocationContext, InvocationSubject
from opsmesh_sdk.extensions.hooks import HookInput
from opsmesh_sdk.extensions.package import ExtensionExports
from opsmesh_sdk.host.events import EventPublisher
from opsmesh_sdk.host.jobs import JobSubmitter
from opsmesh_sdk.host.models import ModelOperator
from opsmesh_sdk.host.observability import ObservabilityReporter
from opsmesh_sdk.host.state import StateStore
from opsmesh_sdk.transport.contracts import Transport
from opsmesh_sdk.values import ScopeRef


def discover(namespace: str, context: InvocationContext | None) -> ExtensionExports:
    entries = list(importlib.metadata.entry_points(group="opsmesh.extensions", name=namespace))
    if len(entries) != 1:
        raise ValueError("Exactly one declared package entry point is required")
    exports = entries[0].load()(context)
    if not isinstance(exports, ExtensionExports) or exports.manifest.package_name != namespace:
        raise ValueError("The entry point returned an invalid extension declaration")
    return exports


async def invoke(message: dict[str, Any], transport: Transport) -> dict[str, Any]:
    """Execute one envelope with its own bounded delegation transport."""
    namespace = message["namespace"]
    context = None
    if message["action"] == "invoke":
        context = InvocationContext(
            InvocationSubject(**message["subject"]),
            scope=ScopeRef(**message["scope"]),
            host_state=StateStore(transport),
            host_models=ModelOperator(transport),
            host_jobs=JobSubmitter(transport),
            host_events=EventPublisher(transport),
            host_observability=ObservabilityReporter(transport),
            deadline_seconds=message["deadline_seconds"],
            configuration=message["configuration"],
            platform=PlatformClient("", transport=transport, api_prefix=message["api_prefix"]),
        )
    exports = discover(namespace, context)
    if message["action"] == "discover":
        return exports.manifest.model_dump(mode="json")
    if message["kind"] == "hook":
        hook = next(hook for hook in exports.hooks if hook.export.export_id == message["export_id"])
        return (await hook.invoke(HookInput.model_validate(message["arguments"]))).model_dump(
            mode="json"
        )
    operation = next(op for op in exports.operations if op.export_id == message["export_id"])
    return (await operation.invoke(message["arguments"])).model_dump(mode="json")


