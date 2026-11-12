"""Public framework access, event, and trusted plugin interfaces."""

from opsmesh.framework.plugins import CommandGroup as CommandGroup
from opsmesh.framework.plugins import MatcherGroup as MatcherGroup
from opsmesh.framework.plugins import PluginAdmission as PluginAdmission
from opsmesh.framework.plugins import PluginLoadDenied as PluginLoadDenied
from opsmesh.framework.plugins import PluginLoadRequest as PluginLoadRequest
from opsmesh.framework.plugins import PluginReloadError as PluginReloadError
from opsmesh.framework.plugins import (
    clear_plugin_admission as clear_plugin_admission,
)
from opsmesh.framework.plugins import (
    get_available_plugin_names as get_available_plugin_names,
)
from opsmesh.framework.plugins import get_loaded_plugins as get_loaded_plugins
from opsmesh.framework.plugins import get_plugin as get_plugin
from opsmesh.framework.plugins import (
    get_plugin_by_module_name as get_plugin_by_module_name,
)
from opsmesh.framework.plugins import get_plugin_config as get_plugin_config
from opsmesh.framework.plugins import load_all_plugins as load_all_plugins
from opsmesh.framework.plugins import load_from_json as load_from_json
from opsmesh.framework.plugins import load_from_toml as load_from_toml
from opsmesh.framework.plugins import load_plugin as load_plugin
from opsmesh.framework.plugins import load_plugins as load_plugins
from opsmesh.framework.plugins import on as on
from opsmesh.framework.plugins import on_command as on_command
from opsmesh.framework.plugins import on_endswith as on_endswith
from opsmesh.framework.plugins import on_fullmatch as on_fullmatch
from opsmesh.framework.plugins import on_keyword as on_keyword
from opsmesh.framework.plugins import on_message as on_message
from opsmesh.framework.plugins import on_metaevent as on_metaevent
from opsmesh.framework.plugins import on_notice as on_notice
from opsmesh.framework.plugins import on_regex as on_regex
from opsmesh.framework.plugins import on_request as on_request
from opsmesh.framework.plugins import on_shell_command as on_shell_command
from opsmesh.framework.plugins import on_startswith as on_startswith
from opsmesh.framework.plugins import on_type as on_type
from opsmesh.framework.plugins import (
    register_plugin_manager as register_plugin_manager,
)
from opsmesh.framework.plugins import require as require
from opsmesh.framework.plugins import (
    set_plugin_admission as set_plugin_admission,
)

from .access import channel as channel
from .access import channels as channels
from .access import get_adapter as get_adapter
from .access import get_adapters as get_adapters
from .access import get_app as get_app
from .access import get_asgi as get_asgi
from .lifecycle import on_ready as on_ready
from .lifecycle import on_shutdown as on_shutdown
from .lifecycle import on_startup as on_startup
from .lifecycle import on_stopping as on_stopping
from .runtime import get_driver as get_driver

__version__ = "0.1.0"

from opsmesh.framework.plugins.hooks import on_plugin_loaded as on_plugin_loaded
