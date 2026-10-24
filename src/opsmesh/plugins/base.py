"""Resource adapter contract, independent of Agent and gateway implementations."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from opsmesh_sdk import PluginContext

from opsmesh.shared.serialization import payload_digest


def plugin_generation(context: PluginContext) -> str:
    return payload_digest(
        {
            "id": context.manifest.id,
            "version": context.manifest.version,
            "config": dict(context.config),
            "credentials": dict(context.credential_refs),
        }
    )


@dataclass(frozen=True)
class PreparedResource:
    id: str
    plugin_id: str
    kind: str
    name: str
    value: Any = field(repr=False)
    metadata: dict[str, Any] = field(default_factory=dict)


class PluginAdapter(ABC):
    """Prepare declarations only; running resources belong to their feature owner."""

    kind: str

    @abstractmethod
    def prepare(self, context: PluginContext, name: str, registration: object) -> PreparedResource:
        raise NotImplementedError

    def release(self, resource: PreparedResource) -> None:
        """Release preparation-owned resources after the generation is retired."""
        return None
