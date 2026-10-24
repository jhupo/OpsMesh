"""Explicit adapters and complete, unpublished resource generations."""

from dataclasses import dataclass, field

from opsmesh_sdk import PluginContext

from opsmesh.plugins.base import PluginAdapter, PreparedResource, plugin_generation


@dataclass
class PluginRegistry:
    adapters: dict[str, PluginAdapter] = field(default_factory=dict)

    def register(self, adapter: PluginAdapter) -> None:
        if not adapter.kind or adapter.kind in self.adapters:
            raise ValueError("Duplicate or empty plugin adapter kind")
        self.adapters[adapter.kind] = adapter

    def prepare(self, contexts: tuple[PluginContext, ...]) -> dict[str, PreparedResource]:
        prepared: dict[str, PreparedResource] = {}
        try:
            for context in contexts:
                declarations = context.contributions()
                if not any(declarations.values()):
                    raise ValueError("Plugin setup did not register any resources")
                declared = context.manifest.kind
                registered_kind = declarations.get(declared)
                if declared != "plugin" and not registered_kind:
                    raise ValueError("Plugin setup did not register its declared kind")
                for kind, registrations in declarations.items():
                    if not registrations:
                        continue
                    adapter = self.adapters.get(kind)
                    if adapter is None:
                        raise ValueError(f"No adapter registered for resource kind: {kind}")
                    for name, registration in sorted(registrations.items()):
                        resource = adapter.prepare(context, name, registration)
                        resource.metadata["generation"] = plugin_generation(context)
                        if resource.id in prepared:
                            adapter.release(resource)
                            raise ValueError("Duplicate plugin resource ID")
                        prepared[resource.id] = resource
        except BaseException:
            self.release(prepared)
            raise
        return prepared

    def release(self, resources: dict[str, PreparedResource]) -> None:
        errors = []
        for resource in reversed(list(resources.values())):
            try:
                self.adapters[resource.kind].release(resource)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("Plugin resource release failed", errors)
