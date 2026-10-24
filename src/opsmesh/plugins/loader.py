import importlib.util
import inspect
import sys
from pathlib import Path

from opsmesh_sdk.registration import PluginContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Plugin
from .packages import package_path
from .registry import PluginRegistry


def load(
    session: Session, root: Path, registry: PluginRegistry, installation_id: str
) -> PluginContext:
    with registry.load_lock:
        row = session.scalar(
            select(Plugin).where(Plugin.id == installation_id, Plugin.enabled.is_(True))
        )
        if row is None:
            from opsmesh.shared.errors import NotFoundError

            raise NotFoundError("Plugin is disabled or unavailable")
        if installation_id in registry.items:
            return registry.get(installation_id)
        directory = package_path(root, row.id)
        data = root / "users" / row.owner_id / "plugins" / row.name / "data"
        data.mkdir(parents=True, exist_ok=True)
        module_name = "opsmesh_installed_" + row.id.replace("-", "_")
        spec = importlib.util.spec_from_file_location(
            module_name, directory / "main.py", submodule_search_locations=[str(directory)]
        )
        if spec is None or spec.loader is None:
            raise ValueError("Plugin module cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        context = PluginContext(row.name, row.version, directory, data, {})
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            setup = getattr(module, "setup", None)
            if not callable(setup) or inspect.iscoroutinefunction(setup):
                raise ValueError("Plugin must expose synchronous setup(ctx)")
            result = setup(context)
            if result is not None:
                raise ValueError("setup(ctx) must return None")
            for entry in context.entries.values():
                if not inspect.iscoroutinefunction(entry.handler) or (
                    entry.resume is not None and not inspect.iscoroutinefunction(entry.resume)
                ):
                    raise ValueError("Entry and resume handlers must be asynchronous")
            registry.items[row.id] = context
        except BaseException:
            sys.modules.pop(module_name, None)
            raise
        return context


async def close(registry: PluginRegistry) -> None:
    errors = []
    for context in reversed(list(registry.items.values())):
        for callback in reversed(context.shutdown):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                errors.append(exc)
    registry.items.clear()
    if errors:
        raise ExceptionGroup("Plugin shutdown failed", errors)
