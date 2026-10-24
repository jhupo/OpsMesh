"""Tool factories and their catalog; no imports or discovery of executable packages."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agents import Tool, function_tool
from pydantic import Field, JsonValue

from opsmesh.agents.config.base import ConfigValue, Identifier

ToolFactory = Callable[[dict[str, JsonValue]], Tool]


class ToolInfo(ConfigValue):
    id: Identifier
    name: str
    description: str = ""
    source: str = "system"
    revision: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    output_schema: dict[str, JsonValue] = Field(default_factory=dict)
    effects: list[str] = Field(default_factory=list)
    requires_approval: bool = False
    admin_only: bool = False
    timeout_seconds: int = Field(default=60, ge=1, le=300)


@dataclass(frozen=True)
class ToolRegistration:
    info: ToolInfo
    factory: ToolFactory


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolRegistration] = {}

    def register(self, info: ToolInfo, factory: ToolFactory, *, replace: bool = False) -> None:
        if info.id in self._tools and not replace:
            raise ValueError(f"Tool already registered: {info.id}")
        self._tools[info.id] = ToolRegistration(info.model_copy(deep=True), factory)

    def register_function(
        self,
        tool_id: str,
        function: Callable[..., Any],
        *,
        source: str = "system",
        replace: bool = False,
    ) -> None:
        tool = function_tool(function)

        def factory(config: dict[str, JsonValue]) -> Tool:
            if config:
                raise ValueError("A configured function tool requires an explicit factory")
            return function_tool(function)

        self.register(
            ToolInfo(
                id=tool_id,
                name=tool.name,
                description=tool.description,
                source=source,
                parameters=tool.params_json_schema,
            ),
            factory,
            replace=replace,
        )

    def get(self, tool_id: str) -> ToolInfo:
        return self._tools[tool_id].info.model_copy(deep=True)

    def list(self) -> list[ToolInfo]:
        return [self.get(identifier) for identifier in sorted(self._tools)]

    def create(self, tool_id: str, config: dict[str, JsonValue]) -> Tool:
        from copy import deepcopy

        return self._tools[tool_id].factory(deepcopy(config))

    def remove(self, tool_id: str) -> None:
        del self._tools[tool_id]
