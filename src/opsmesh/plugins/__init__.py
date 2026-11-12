"""Public plugin loading, configuration, and matcher factory interfaces."""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from opsmesh.plugins.hooks import on_plugin_loaded as on_plugin_loaded

from .admission import PluginAdmission as PluginAdmission
from .admission import PluginLoadRequest as PluginLoadRequest
from .admission import clear_plugin_admission as clear_plugin_admission
from .admission import set_plugin_admission as set_plugin_admission
from .config import get_plugin_config as get_plugin_config
from .errors import PluginLoadDenied as PluginLoadDenied
from .errors import PluginReloadError as PluginReloadError
from .load import inherit_supported_adapters as inherit_supported_adapters
from .load import load_all_plugins as load_all_plugins
from .load import load_from_json as load_from_json
from .load import load_from_toml as load_from_toml
from .load import load_plugin as load_plugin
from .load import load_plugins as load_plugins
from .load import require as require
from .manager import PluginManager as PluginManager
from .model import Plugin as Plugin
from .model import PluginMetadata as PluginMetadata
from .model import PluginStatus as PluginStatus
from .on import CommandGroup as CommandGroup
from .on import MatcherGroup as MatcherGroup
from .on import on as on
from .on import on_command as on_command
from .on import on_endswith as on_endswith
from .on import on_fullmatch as on_fullmatch
from .on import on_keyword as on_keyword
from .on import on_message as on_message
from .on import on_metaevent as on_metaevent
from .on import on_notice as on_notice
from .on import on_regex as on_regex
from .on import on_request as on_request
from .on import on_shell_command as on_shell_command
from .on import on_startswith as on_startswith
from .on import on_type as on_type
from .registry import get_available_plugin_names as get_available_plugin_names
from .registry import get_loaded_plugins as get_loaded_plugins
from .registry import get_plugin as get_plugin
from .registry import get_plugin_by_module_name as get_plugin_by_module_name
from .registry import register_plugin_manager as register_plugin_manager
from .registry import revoke_plugin as revoke_plugin
