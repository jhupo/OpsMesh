"""Tool adapter contract: expose a declared operation as a model-callable tool.

The adapter derives the OpenAI FunctionTool schema from the operation's frozen
declaration. It never invokes the tool directly; the server authorizes and
runs the operation and returns a content result.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from opsmesh_sdk.extensions.operations import Operation

MAX_TOOL_NAME_LENGTH = 64


class ToolSchema(BaseModel):
    """The function-tool schema derived from an operation declaration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=MAX_TOOL_NAME_LENGTH)
    description: str = Field(min_length=1, max_length=2000)
    parameters: dict[str, JsonValue]


class ToolContent(BaseModel):
    """One tool result content block handed back to the model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content_type: str = Field(default="text", max_length=50)
    text: str | None = None
    ref: str | None = Field(default=None, max_length=512)
    is_error: bool = False


def tool_schema_for_operation(operation: Operation) -> ToolSchema:
    """Derive the tool schema from the operation's frozen declaration."""
    export = operation.export
    name = export.export_id
    if len(name) > MAX_TOOL_NAME_LENGTH:
        raise ValueError("operation export_id exceeds the tool name bound")
    return ToolSchema(
        name=name,
        description=export.description,
        parameters=export.input_schema.model_dump_schema(),
    )


__all__ = ["MAX_TOOL_NAME_LENGTH", "ToolContent", "ToolSchema", "tool_schema_for_operation"]
