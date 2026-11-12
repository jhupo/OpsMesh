"""Public framework access, event, and trusted plugin interfaces."""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from opsmesh.plugins import CommandGroup as CommandGroup
from opsmesh.plugins import MatcherGroup as MatcherGroup
from opsmesh.plugins import PluginAdmission as PluginAdmission
from opsmesh.plugins import PluginLoadDenied as PluginLoadDenied
from opsmesh.plugins import PluginLoadRequest as PluginLoadRequest
from opsmesh.plugins import PluginReloadError as PluginReloadError
from opsmesh.plugins import (
    clear_plugin_admission as clear_plugin_admission,
)
from opsmesh.plugins import (
    get_available_plugin_names as get_available_plugin_names,
)
from opsmesh.plugins import get_loaded_plugins as get_loaded_plugins
from opsmesh.plugins import get_plugin as get_plugin
from opsmesh.plugins import (
    get_plugin_by_module_name as get_plugin_by_module_name,
)
from opsmesh.plugins import get_plugin_config as get_plugin_config
from opsmesh.plugins import load_all_plugins as load_all_plugins
from opsmesh.plugins import load_from_json as load_from_json
from opsmesh.plugins import load_from_toml as load_from_toml
from opsmesh.plugins import load_plugin as load_plugin
from opsmesh.plugins import load_plugins as load_plugins
from opsmesh.plugins import on as on
from opsmesh.plugins import on_command as on_command
from opsmesh.plugins import on_endswith as on_endswith
from opsmesh.plugins import on_fullmatch as on_fullmatch
from opsmesh.plugins import on_keyword as on_keyword
from opsmesh.plugins import on_message as on_message
from opsmesh.plugins import on_metaevent as on_metaevent
from opsmesh.plugins import on_notice as on_notice
from opsmesh.plugins import on_regex as on_regex
from opsmesh.plugins import on_request as on_request
from opsmesh.plugins import on_shell_command as on_shell_command
from opsmesh.plugins import on_startswith as on_startswith
from opsmesh.plugins import on_type as on_type
from opsmesh.plugins import (
    register_plugin_manager as register_plugin_manager,
)
from opsmesh.plugins import require as require
from opsmesh.plugins import (
    set_plugin_admission as set_plugin_admission,
)

from .drivers import get_driver as get_driver
from .lifecycle import on_quiescing as on_quiescing
from .lifecycle import on_ready as on_ready
from .lifecycle import on_shutdown as on_shutdown
from .lifecycle import on_started as on_started
from .lifecycle import on_startup as on_startup
from .lifecycle import on_stopping as on_stopping

__version__ = "0.5.0"

from opsmesh.plugins.hooks import on_plugin_loaded as on_plugin_loaded
