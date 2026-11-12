"""Plugin file spaces and public file operations."""

from opsmesh.framework.plugins import PluginMetadata

from .config import CONFIG_NAMESPACE, Settings
from .errors import (
    ArchiveRejected,
    FilesystemClosed,
    FilesystemError,
    PathNotFound,
    PathRejected,
)
from .files import (
    ArchiveExport,
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
)

__all__ = [
    "CONFIG_NAMESPACE",
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
