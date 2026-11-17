"""Create consumer-owned protocol connections from dependency configuration."""

from importlib import import_module

from pydantic import BaseModel

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
        for protocol in ("api", "qq", "dingtalk"):
            accounts = getattr(settings, protocol)
            if accounts:
                _configure_protocol(protocol, accounts)

    get_driver().on_startup(prepare)


def _configure_protocol(protocol: str, accounts: dict[str, BaseModel]) -> None:
    """Let the protocol validate credentials and acquire its connections."""
    if protocol == "api":
        from opsmesh.plugins import get_current_plugin

        from ..api import APIChannel, get_entrances
        from ..api.errors import APIConfigurationError
        from .hooks import transports

        owner = get_current_plugin()
        for name, settings in accounts.items():
            entrance = get_entrances().get(owner.id_, name)
            if entrance is None:
                raise APIConfigurationError(
                    f"HTTP connection {name} requires message handlers "
                    "and identity"
                )
            channel = APIChannel(settings, entrance)
            transports.use(channel)
        return
    module = import_module(f"opsmesh.plugins.channels.{protocol}.adapter")
    adapter_type = (
        module.QQAdapter if protocol == "qq" else module.DingTalkAdapter
    )
    adapter = adapter_type.configured()
    for name, settings in accounts.items():
        adapter.configure(settings, name=name)
