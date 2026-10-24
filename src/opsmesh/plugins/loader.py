"""Load trusted entry points into generation-local Python namespaces."""

import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from uuid import uuid4

from opsmesh_sdk import PluginContext, PluginManifest
from opsmesh_sdk.manifest import read_manifest


@dataclass(frozen=True)
class LoadedPlugin:
    context: PluginContext
    namespace: str

    def unload(self) -> None:
        for name in tuple(sys.modules):
            if name == self.namespace or name.startswith(f"{self.namespace}."):
                sys.modules.pop(name, None)


def load_plugin(
    manifest: PluginManifest, directory: Path, data_root: Path, config: dict[str, object]
) -> LoadedPlugin:
    if directory.is_symlink() or read_manifest(directory) != manifest:
        raise ValueError("Installed plugin manifest changed")
    namespace = f"_opsmesh_plugin_{manifest.id.replace('-', '_')}_{uuid4().hex}"
    package = ModuleType(namespace)
    package.__path__ = [str(directory)]
    data = data_root / manifest.id
    try:
        data.mkdir(parents=True)
    except FileExistsError:
        if not data.is_dir():
            raise
    else:
        data.chmod(0o2770)
    loaded = LoadedPlugin(PluginContext(manifest, directory, data, config), namespace)
    sys.modules[namespace] = package
    try:
        module = importlib.import_module(f"{namespace}.{manifest.entry[:-3].replace('/', '.')}")
        setup = module.setup
        if not callable(setup) or inspect.iscoroutinefunction(setup):
            raise ValueError("Plugin entry must export synchronous setup(ctx)")
        result = setup(loaded.context)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise ValueError("Plugin setup must not return an awaitable")
        return loaded
    except BaseException:
        loaded.unload()
        raise
