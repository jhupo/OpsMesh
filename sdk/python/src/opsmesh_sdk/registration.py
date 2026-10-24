from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents import FunctionTool

from .context import InvocationContext

Handler = Callable[[InvocationContext, Any], Awaitable[Any]]
Factory = Callable[..., Any]


@dataclass(frozen=True)
class Entry:
    handler: Handler
    resume: Handler | None = None


@dataclass
class PluginContext:
    plugin_id: str
    version: str
    package_dir: Path
    data_dir: Path
    config: Mapping[str, Any]
    entries: dict[str, Entry] = field(default_factory=dict)
    tools: dict[str, FunctionTool] = field(default_factory=dict)
    mcps: dict[str, Factory] = field(default_factory=dict)
    skills: dict[str, Path] = field(default_factory=dict)
    channels: dict[str, Factory] = field(default_factory=dict)
    sandboxes: dict[str, Factory] = field(default_factory=dict)
    shutdown: list[Callable[[], Any]] = field(default_factory=list)

    def _register(self, registry: dict[str, Any], name: str, value: Any) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name) or name in registry:
            raise ValueError("Invalid or duplicate contribution name")
        registry[name] = value

    def entry(self, name: str, handler: Handler, *, resume: Handler | None = None) -> None:
        self._register(self.entries, name, Entry(handler, resume))

    def tool(self, name: str, tool: FunctionTool) -> None:
        self._register(self.tools, name, tool)

    def mcp(self, name: str, factory: Factory) -> None:
        self._register(self.mcps, name, factory)

    def skill(self, name: str, *, directory: str) -> None:
        path = (self.package_dir / directory).resolve()
        if not path.is_relative_to(self.package_dir.resolve()) or not (path / "SKILL.md").is_file():
            raise ValueError("Skill must be a package directory containing SKILL.md")
        self._register(self.skills, name, path)

    def channel(self, name: str, factory: Factory) -> None:
        self._register(self.channels, name, factory)

    def sandbox(self, backend: str, factory: Factory) -> None:
        self._register(self.sandboxes, backend, factory)

    def on_shutdown(self, callback: Callable[[], Any]) -> None:
        self.shutdown.append(callback)
