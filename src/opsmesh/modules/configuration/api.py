"""Versioned platform settings and enforceable admission limits."""

import builtins
from typing import Any, Protocol

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class ConfigurationEntry(OpsMeshContract):
    key: str
    value: dict[str, Any]
    revision: int = 1


class AdmissionPolicy(OpsMeshContract):
    max_active_runs: int = Field(default=16, ge=1, le=10000)
    max_input_characters: int = Field(default=100000, ge=1, le=10000000)
    requests_per_minute: int = Field(default=120, ge=1, le=100000)


class PlatformConfiguration(Protocol):
    async def get(self, key: str) -> ConfigurationEntry | None: ...
    async def list(self) -> builtins.list[ConfigurationEntry]: ...
    async def set(
        self, key: str, value: dict[str, Any], expected_revision: int | None = None
    ) -> ConfigurationEntry: ...
    async def admission_policy(self) -> AdmissionPolicy: ...
