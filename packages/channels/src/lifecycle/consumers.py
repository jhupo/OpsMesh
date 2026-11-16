"""Create consumer-owned robot connections from dependency configuration."""

import os
from importlib import import_module

from pydantic import JsonValue

from opsmesh.drivers import get_driver
from opsmesh.plugins import Plugin, get_dependency_config, on_plugin_loaded

from ..config import ChannelConnections


@on_plugin_loaded
def configure_connections(plugin: Plugin) -> None:
    """Bind one startup callback without acquiring network resources."""
    from opsmesh.plugins import get_plugin_by_module_name

    provider = get_plugin_by_module_name(__name__)
    if provider is None or provider.id_ not in plugin.dependencies:
        return

    async def prepare() -> None:
        settings = get_dependency_config(ChannelConnections)
        for protocol in ("qq", "dingtalk"):
            accounts = getattr(settings, protocol)
            if accounts:
                _configure_protocol(protocol, accounts)

    get_driver().on_startup(prepare)


def _configure_protocol(
    protocol: str, accounts: dict[str, dict[str, JsonValue]]
) -> None:
    """Acquire typed account declarations inside the consumer startup scope."""
    module = import_module(f"opsmesh.plugins.channels.{protocol}.connections")
    schema = (
        module.QQConnectionSettings
        if protocol == "qq"
        else module.DingTalkConnectionSettings
    )
    adapter = module._prepare()
    enabled = protocol != "qq" or adapter.settings.enabled
    for name, values in accounts.items():
        settings = schema.model_validate(values)
        reference = (
            settings.secret_ref
            if protocol == "qq"
            else settings.client_secret_ref
        )
        if enabled and not os.environ.get(reference, "").strip():
            raise ValueError(
                f"{protocol}.{name}: missing credential environment variable {reference}"
            )
        account = settings.app_id if protocol == "qq" else settings.client_id
        for declared in adapter.declarations:
            existing = declared.settings
            identifier = (
                existing.app_id if protocol == "qq" else existing.client_id
            )
            if identifier == account:
                raise ValueError(
                    f"{protocol}.{name}: robot account already belongs to {declared.owner_id}"
                )
        module._declare_connection(settings, name=name)
