"""Host-controlled admission for plugin loading.

The host decides which installation identifiers may execute in this process.
The framework never reads installation state itself; it only forwards the
load request before any plugin code is imported.
"""

from collections.abc import Callable
from dataclasses import dataclass

from .errors import PluginLoadDenied


@dataclass(frozen=True)
class PluginLoadRequest:
    """One plugin load proposed by the framework or a dependency."""

    plugin_id: str
    """Stable identifier computed by the framework."""

    module_name: str
    """Dotted module name that will be imported."""

    origin: str | None
    """Resolved source origin, when the module can be located."""


PluginAdmission = Callable[[PluginLoadRequest], bool | None]
"""Return False to deny; None or True allows the load."""


def check_plugin_load(
    admission: PluginAdmission | None, request: PluginLoadRequest
) -> None:
    """Raise when the active environment denies this plugin load."""
    if admission is not None and admission(request) is False:
        raise PluginLoadDenied(
            f'Plugin "{request.plugin_id}" is not enabled for this host'
        )
