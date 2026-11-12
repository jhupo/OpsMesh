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

_admission: PluginAdmission | None = None


def set_plugin_admission(admission: PluginAdmission | None) -> None:
    """Install the process-wide admission check used before imports."""
    global _admission
    _admission = admission


def clear_plugin_admission() -> None:
    """Remove the process-wide admission check."""
    set_plugin_admission(None)


def check_plugin_load(
    plugin_id: str, module_name: str, origin: str | None
) -> None:
    """Raise when the host denies this plugin load.

    There is no admission check by default, preserving standalone framework
    usage. Exceptions raised by the check itself are not swallowed.
    """
    admission = _admission
    if admission is None:
        return
    request = PluginLoadRequest(plugin_id, module_name, origin)
    if admission(request) is False:
        raise PluginLoadDenied(
            f'Plugin "{plugin_id}" is not enabled for this host'
        )
