"""Durable capability catalog facts."""

from typing import Any, Literal

from opsmesh.framework.typing import OpsMeshContract


class CapabilityPublished(OpsMeshContract):
    type: Literal["capability.published"] = "capability.published"
    source: Literal["capabilities"] = "capabilities"
    payload: dict[str, Any]
    schema_version: Literal[1] = 1
