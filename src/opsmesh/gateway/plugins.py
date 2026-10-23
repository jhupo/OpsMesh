"""The generic plugin host installs packages; this adapter validates channels."""

from opsmesh_sdk import PluginContext
from opsmesh_sdk.channels import ChannelAdapter as TransportAdapter
from opsmesh_sdk.registration import ChannelRegistration

from opsmesh.gateway.registry import BUILTINS
from opsmesh.plugins.base import PluginAdapter, PreparedResource


class ChannelAdapter(PluginAdapter):
    kind = "channel"

    def prepare(self, context: PluginContext, name: str, registration: object) -> PreparedResource:
        if not isinstance(registration, ChannelRegistration):
            raise TypeError("Expected channel registration")
        if name in BUILTINS or not issubclass(registration.channel_cls, TransportAdapter):
            raise ValueError("Register a new opsmesh_sdk.channels.ChannelAdapter implementation")
        return PreparedResource(
            f"{context.manifest.id}/channel/{name}",
            context.manifest.id,
            self.kind,
            name,
            registration,
            {
                "label": registration.label,
                "capabilities": registration.channel_cls.capabilities.model_dump(mode="json"),
                "config_fields": [value.model_dump() for value in registration.fields],
            },
        )
