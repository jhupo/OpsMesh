"""Original plugin records with NoneBot-style metadata conventions."""

from __future__ import annotations

import builtins
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from types import ModuleType
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class OpsMeshPluginMetadata:
    name: str
    description: str
    usage: str
    type: str | None = None
    homepage: str | None = None
    config: builtins.type[BaseModel] | None = None
    supported_adapters: set[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class PluginState(StrEnum):
    LOADING = "loading"
    ENABLED = "enabled"
    DISABLED = "disabled"
    FAILED = "failed"


@dataclass(eq=False)
class OpsMeshPlugin:
    name: str
    module_name: str
    module: ModuleType | None = None
    metadata: OpsMeshPluginMetadata | None = None
    state: PluginState = PluginState.LOADING
    config: dict[str, Any] = field(default_factory=dict)
    dependencies: set[str] = field(default_factory=set)
    cleanups: list[Callable[[], None]] = field(default_factory=list, repr=False)

    @property
    def enabled(self) -> bool:
        return self.state == PluginState.ENABLED

    @property
    def id_(self) -> str:
        return self.module_name

    def own(self, cleanup: Callable[[], None]) -> None:
        """Record a reversible registration; arbitrary import side effects aren't undone."""
        self.cleanups.append(cleanup)

    def rollback(self) -> None:
        errors = []
        for cleanup in reversed(self.cleanups):
            try:
                cleanup()
            except Exception as exc:
                errors.append(exc)
        self.cleanups.clear()
        if errors:
            raise ExceptionGroup("OpsMeshPlugin registration rollback failed", errors)
