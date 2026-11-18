"""Plugin metadata and ownership models."""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from types import ModuleType
from typing import TYPE_CHECKING, Any, Type  # noqa: UP035

from pydantic import BaseModel

if TYPE_CHECKING:
    from opsmesh.matchers import Matcher

    from .invocation import PluginCall
    from .lifecycle import LifecycleHook
    from .manager import PluginManager


@dataclass(eq=False)
class PluginMetadata:
    """Metadata supplied by the plugin author."""

    name: str
    """The plugin display name."""
    description: str
    """A description of the plugin's functionality."""
    usage: str
    """Instructions for using the plugin."""
    type: str | None = None
    """The plugin category used by marketplaces."""
    homepage: str | None = None
    """The plugin homepage URL."""
    config: Type[BaseModel] | None = None  # noqa: UP006
    """The plugin configuration model."""
    extra: dict[Any, Any] = field(default_factory=dict)
    """Author metadata and native objects consumed by application modules."""

    dependency_config: Type[BaseModel] | None = None  # noqa: UP006
    """Schema for settings supplied by each consuming plugin."""
    config_file: str | None = None
    """Initial JSON configuration path relative to the plugin entry package."""


class PluginStatus(StrEnum):
    """The runtime lifecycle state of a loaded plugin."""

    LOADED = "loaded"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"
    BLOCKED = "blocked"
    STOPPED = "stopped"
    STOPPING = "stopping"


@dataclass(eq=False)
class Plugin:
    """A loaded plugin and its registered resources."""

    name: str
    """The plugin name, derived from its file or directory name."""
    module: ModuleType
    """The plugin module object."""
    module_name: str
    """The dotted module path."""
    manager: "PluginManager"
    """The manager that loaded this plugin."""
    matcher: set[type["Matcher"]] = field(default_factory=set)
    """Matcher classes registered while loading this plugin."""
    parent_plugin: "Plugin | None" = None
    """The parent plugin, if any."""
    sub_plugins: set["Plugin"] = field(default_factory=set)
    """Child plugins."""
    metadata: PluginMetadata | None = None
    """Plugin metadata."""
    cleanups: list[Callable[[], None]] = field(default_factory=list, repr=False)
    """Registration removals in acquisition order; never arbitrary effects."""
    config_values: dict[Any, Any] | None = field(default=None, repr=False)
    """Owned startup values, independent of global deployment configuration."""
    dependency_config_values: dict[str, dict[str, Any]] = field(
        default_factory=dict, repr=False
    )
    """Consumer-owned values keyed by dependency identifier or module."""
    dependencies: list[str] = field(default_factory=list)
    """Plugin identifiers required before this plugin may accept work."""
    required: bool = False
    """Whether a startup failure must abort the host."""
    status: PluginStatus = PluginStatus.LOADED
    """The current lifecycle state."""
    failure: BaseException | None = field(default=None, repr=False)
    """The startup failure that prevented readiness, when any."""
    blocked_by: set[str] = field(default_factory=set, repr=False)
    """Dependencies whose failure blocks this plugin."""

    enabled: bool = True
    manually_stopped: bool = False
    """Whether this host requires explicit recovery before activation."""
    generation: int = 0
    """In-memory activation counter, independent of package versions."""
    cleanup_complete: bool = True
    activation_cleanups: list[Callable[[], None]] = field(
        default_factory=list, repr=False
    )
    calls: dict[int, "PluginCall"] = field(default_factory=dict, repr=False)
    completed_shutdown_hooks: set[int] = field(default_factory=set, repr=False)
    lifecycle_hooks: dict[str, list["LifecycleHook"]] = field(
        default_factory=dict, repr=False
    )

    @property
    def ready(self) -> bool:
        """Whether startup completed and the plugin may accept work."""
        return self.status is PluginStatus.READY

    @property
    def id_(self) -> str:
        """The plugin's lookup identifier."""
        return (
            f"{self.parent_plugin.id_}:{self.name}"
            if self.parent_plugin
            else self.name
        )
