"""Prepare each consumer's declared connections inside its own lifespan."""

from __future__ import annotations

from importlib import import_module

from opsmesh import get_driver, on_plugin_loaded
from opsmesh.plugins import Plugin, get_dependency_config
from opsmesh.plugins.registry import get_plugin_by_module_name

from .adapter import ChannelAdapter
from .channel import Channel
from .config import ChannelConnections

_ADAPTERS = {
    "api": "APIAdapter",
    "qq": "QQAdapter",
    "dingtalk": "DingTalkAdapter",
}


def adapter_type(protocol: str) -> type[ChannelAdapter]:
    """Resolve one protocol adapter class without importing vendor SDKs."""
    try:
        name = _ADAPTERS[protocol]
    except KeyError:
        raise ValueError(f"Unsupported channel protocol: {protocol}") from None
    module = import_module(f"{__package__}.{protocol}.adapter")
    return getattr(module, name)


def protocol_adapter(protocol: str) -> ChannelAdapter:
    """Return the adapter that prepared the protocol's connections."""
    adapter_class = adapter_type(protocol)
    try:
        return get_driver().get_adapter(adapter_class)
    except KeyError:
        raise LookupError(f"{protocol} connections are not prepared") from None


def _ensure_adapter(protocol: str) -> ChannelAdapter:
    try:
        return protocol_adapter(protocol)
    except LookupError:
        return get_driver().register_adapter(adapter_type(protocol))


@on_plugin_loaded
def configure_connections(plugin: Plugin) -> None:
    """Prepare the connections declared in one consumer's dependencies."""
    provider = get_plugin_by_module_name(__name__)
    if provider is None or provider.id_ not in plugin.dependencies:
        return

    async def prepare() -> None:
        settings = get_dependency_config(ChannelConnections)
        declared: list[Channel] = []
        try:
            for protocol in _ADAPTERS:
                accounts = getattr(settings, protocol)
                if not accounts:
                    continue
                adapter = _ensure_adapter(protocol)
                for name, account in accounts.items():
                    declared.append(adapter.declare(account, name=name))
            for channel in declared:
                await channel.prepare()
        except BaseException as error:
            errors: list[BaseException] = [error]
            for channel in reversed(declared):
                try:
                    await channel.close()
                except BaseException as cleanup_error:
                    errors.append(cleanup_error)
            if len(errors) > 1:
                raise BaseExceptionGroup(
                    "Channel preparation and cleanup failed", errors
                ) from None
            raise

    get_driver().on_startup(prepare)
