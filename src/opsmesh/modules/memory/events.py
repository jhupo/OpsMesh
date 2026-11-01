"""Memory task and publication facts for transactional outbox delivery."""

from typing import Any, Literal

from opsmesh.framework.typing import OpsMeshContract


class MemoryGenerationRequested(OpsMeshContract):
    type: Literal["memory.generation.requested"] = "memory.generation.requested"
    source: Literal["memory"] = "memory"
    payload: dict[str, Any]
    actor_ref: str
    schema_version: Literal[1] = 1


class MemoryRevisionPublished(OpsMeshContract):
    type: Literal["memory.revision.published"] = "memory.revision.published"
    source: Literal["memory"] = "memory"
    payload: dict[str, Any]
    schema_version: Literal[1] = 1
