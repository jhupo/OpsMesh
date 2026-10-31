"""Immutable capability versions and an authorized, replay-safe tool gateway."""

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.infrastructure.database.availability import PersistentVersionAvailability
from opsmesh.modules.capabilities.api import (
    ToolDefinition,
)
from opsmesh.modules.capabilities.events import CapabilityPublished


class PersistentCapabilityCatalog:
    def __init__(self, database: AsyncDatabase) -> None:
        self._tools = VersionedRecordStore(database, "capability_tools")
        self.availability = PersistentVersionAvailability(database, "capability_tools")

    async def register_tool(self, definition: ToolDefinition) -> ToolDefinition:
        if not definition.name or not definition.version or "@" in definition.name:
            raise OpsMeshError("CONFIG_INVALID", "Tool name and version are required")
        try:
            Draft202012Validator.check_schema(definition.input_schema)
        except SchemaError as error:
            raise OpsMeshError("CONFIG_INVALID", "Invalid tool input schema") from error
        existing = await self._tools.get(definition.ref)
        if existing is not None:
            if existing.data != definition.model_dump(mode="json"):
                raise OpsMeshError("CONFLICT", "Tool versions are immutable")
            return ToolDefinition.model_validate(existing.data)
        await self._tools.create(
            definition.ref,
            definition.model_dump(mode="json"),
            events=(
                CapabilityPublished(payload={"ref": definition.ref, "kind": "tool"}).model_dump(
                    mode="json"
                ),
            ),
        )
        return definition

    async def get_tool(self, ref: str, *, include_disabled: bool = False) -> ToolDefinition:
        if not include_disabled:
            await self.availability.require_enabled(ref)
        record = await self._tools.get(ref)
        if record is None:
            raise OpsMeshError("NOT_FOUND", "Tool version not found")
        return ToolDefinition.model_validate(record.data)

    async def list_tools(self) -> tuple[ToolDefinition, ...]:
        return tuple(ToolDefinition.model_validate(row.data) for row in await self._tools.list())
