"""Native and plugin adapters share one dependency-free channel contract."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from opsmesh_sdk import PluginContext
from opsmesh_sdk.channels import ChannelAdapter, ChannelCapabilities, EventSink
from opsmesh_sdk.registration import ConfigField, resolve_config

from opsmesh.gateway.adapters.dingtalk.adapter import DingTalkAdapter
from opsmesh.gateway.adapters.qq.adapter import QQAdapter
from opsmesh.gateway.contracts import ChannelDefinition
from opsmesh.gateway.messaging.media import GatewayMedia
from opsmesh.shared.serialization import payload_digest


@dataclass(frozen=True)
class ChannelKindInfo:
    kind: str
    label: str
    capabilities: ChannelCapabilities
    plugin_id: str | None = None
    config_fields: tuple[ConfigField, ...] = ()


BUILTINS: dict[str, type[ChannelAdapter]] = {"qq": QQAdapter, "dingtalk": DingTalkAdapter}
BUILTIN_FIELDS = {
    "qq": (
        ConfigField(name="app_id", required=True),
        ConfigField(name="app_secret", type="password", required=True),
        ConfigField(name="sandbox", type="boolean", default=False),
        ConfigField(name="intents", type="number", default=(1 << 25) | (1 << 30) | (1 << 12)),
    ),
    "dingtalk": (
        ConfigField(name="app_key", required=True),
        ConfigField(name="app_secret", type="password", required=True),
        ConfigField(name="robot_code", required=True),
        ConfigField(name="corp_id", required=True),
    ),
}


class ChannelRegistry:
    def __init__(self, contexts: Iterable[PluginContext] = ()) -> None:
        self.classes = dict(BUILTINS)
        self.infos = {
            kind: ChannelKindInfo(
                kind, kind, adapter.capabilities, config_fields=BUILTIN_FIELDS[kind]
            )
            for kind, adapter in BUILTINS.items()
        }
        self.versions: dict[str, tuple[str, str, dict[str, object]]] = {}
        for context in contexts:
            for kind, registration in context.channels.items():
                if kind in self.classes:
                    raise ValueError("Channel kind already registered")
                self.classes[kind] = registration.channel_cls
                self.infos[kind] = ChannelKindInfo(
                    kind,
                    registration.label,
                    registration.channel_cls.capabilities,
                    context.manifest.id,
                    registration.fields,
                )
                self.versions[kind] = (
                    context.manifest.id,
                    context.manifest.version,
                    dict(context.config),
                )

    def list(self) -> list[ChannelKindInfo]:
        return [self.infos[kind] for kind in sorted(self.infos)]

    def generation(self, kind: str) -> str:
        if kind not in self.infos:
            raise ValueError("Unknown gateway channel kind")
        capabilities = self.infos[kind].capabilities.model_dump(mode="json")
        capabilities["management"] = sorted(self.infos[kind].capabilities.management)
        return payload_digest(
            {"kind": kind, "plugin": self.versions.get(kind), "capabilities": capabilities}
        )

    def configuration(self, kind: str, config: dict[str, Any]) -> dict[str, Any]:
        if kind not in self.classes:
            raise ValueError("Unknown gateway channel kind")
        info = self.infos[kind]
        if info.config_fields:
            config = dict(resolve_config(info.config_fields, config))
        return self.classes[kind].validate_config(config)

    def validate(self, definition: ChannelDefinition) -> None:
        if definition.kind not in self.classes:
            raise ValueError("Unknown gateway channel kind")
        capabilities = self.infos[definition.kind].capabilities
        if definition.policy.streaming and not capabilities.streaming:
            raise ValueError("The channel does not support streaming")
        if definition.policy.typing_interval_seconds and not capabilities.typing:
            raise ValueError("The channel does not support typing notifications")
        for group in definition.policy.groups.values():
            if not group.management.actions.issubset(capabilities.management):
                raise ValueError("The channel does not implement the granted management actions")
            if any(
                not grant.actions.issubset(group.management.actions)
                for grant in group.management.grants.values()
            ):
                raise ValueError("Plugin grants must be a subset of the conversation's actions")
        if definition.enabled or definition.config:
            self.configuration(definition.kind, dict(definition.config))

    def create(
        self, definition: ChannelDefinition, sink: EventSink, media: GatewayMedia
    ) -> ChannelAdapter:
        adapter = self.classes[definition.kind](
            self.configuration(definition.kind, dict(definition.config)), sink, media
        )
        if adapter.capabilities.management and adapter.management is None:
            raise ValueError("Management capabilities require a ConversationManagement instance")
        return adapter
