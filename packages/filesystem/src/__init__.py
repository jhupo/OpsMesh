"""Plugin file spaces and public file operations."""

from opsmesh import get_driver
from opsmesh.plugin import PluginMetadata, get_plugin_by_module_name

from .config import Settings
from .errors import (
    ArchiveRejected,
    FilesystemClosed,
    FilesystemError,
    PathNotFound,
    PathRejected,
)
from .files import (
    ArchiveExport,
    _shutdown,
    _startup,
    cache_dir,
    cache_file,
    config_dir,
    config_file,
    data_dir,
    data_file,
    delete,
    export_archive,
    extract_archive,
    publish_directory,
    read_file,
    write_file,
)

__plugin_meta__ = PluginMetadata(
    name="filesystem",
    description="Per-plugin data, cache, and config directories",
    usage="Load this plugin before plugins that persist or read files.",
    type="library",
    config=Settings,
    config_file="config.json",
)

if get_plugin_by_module_name(__name__) is not None:
    get_driver().on_startup(_startup)
    get_driver().on_shutdown(_shutdown)

__all__ = [
    "Settings",
    "ArchiveRejected",
    "FilesystemClosed",
    "FilesystemError",
    "PathNotFound",
    "PathRejected",
    "ArchiveExport",
    "cache_dir",
    "cache_file",
    "config_dir",
    "config_file",
    "data_dir",
    "data_file",
    "delete",
    "export_archive",
    "extract_archive",
    "publish_directory",
    "read_file",
    "write_file",
]
