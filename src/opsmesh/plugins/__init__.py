"""Public plugin loading, configuration, and matcher factory interfaces."""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

from opsmesh.plugins.hooks import on_plugin_loaded as on_plugin_loaded

from .admission import PluginAdmission as PluginAdmission
from .admission import PluginLoadRequest as PluginLoadRequest
from .config import get_dependency_config as get_dependency_config
from .config import get_plugin_config as get_plugin_config
from .errors import PluginLoadDenied as PluginLoadDenied
from .errors import PluginReloadError as PluginReloadError
from .invocation import plugin_call as plugin_call
from .lifecycle import ASYNC_LIFESPAN_FUNC as ASYNC_LIFESPAN_FUNC
from .lifecycle import LIFESPAN_FUNC as LIFESPAN_FUNC
from .lifecycle import SYNC_LIFESPAN_FUNC as SYNC_LIFESPAN_FUNC
from .lifecycle import on_quiescing as on_quiescing
from .lifecycle import on_ready as on_ready
from .lifecycle import on_shutdown as on_shutdown
from .lifecycle import on_started as on_started
from .lifecycle import on_startup as on_startup
from .lifecycle import on_stopping as on_stopping
from .load import load_all_plugins as load_all_plugins
from .load import load_from_json as load_from_json
from .load import load_from_toml as load_from_toml
from .load import load_plugin as load_plugin
from .load import load_plugins as load_plugins
from .load import require as require
from .manager import PluginManager as PluginManager
from .manager import get_available_plugin_names as get_available_plugin_names
from .manager import get_plugin_manager as get_plugin_manager
from .manager import plugin_environment as plugin_environment
from .manager import register_plugin_manager as register_plugin_manager
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
from .registry import get_current_plugin as get_current_plugin
from .registry import get_loaded_plugins as get_loaded_plugins
from .registry import get_plugin as get_plugin
from .registry import get_plugin_by_module_name as get_plugin_by_module_name
from .registry import plugin_scope as plugin_scope
from .registry import revoke_plugin as revoke_plugin
