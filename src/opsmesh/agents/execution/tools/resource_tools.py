"""Argument mapping for governed memory and knowledge tools."""

from uuid import UUID

from opsmesh.agents.execution.contracts import AgentResourceGrant
from opsmesh.agents.execution.tools.arguments import (
    bool_argument,
    int_argument,
    optional_int_argument,
    optional_str_argument,
    optional_uuid_argument,
    str_argument,
    uuid_argument,
)
from opsmesh.capabilities.tools.contracts import ToolContext
from opsmesh.capabilities.tools.service import ProductToolService
from opsmesh.resources.memory.authorization import memory_read_scopes, memory_write_scopes

RESOURCE_TOOL_NAMES = frozenset(
    {
        "list_semantic_memory",
        "get_semantic_memory",
        "list_semantic_memory_revisions",
        "search_knowledge",
        "list_knowledge",
        "get_knowledge",
        "create_knowledge",
        "update_knowledge",
        "archive_knowledge",
        "get_knowledge_ingestion",
    }
)


def execute_resource_tool(
    service: ProductToolService,
    context: ToolContext,
    tool_name: str,
    arguments: dict[str, object],
    grants: tuple[AgentResourceGrant, ...],
    allowed_file_ids: set[UUID],
) -> dict[str, object]:
    read_scopes = memory_read_scopes(grants)
    if tool_name == "list_semantic_memory":
        return service.memory.list_semantic_memory(
            context,
            access_scopes=read_scopes,
            limit=int_argument(arguments, "limit", default=20),
            offset=int_argument(arguments, "offset", default=0),
            include_archived=bool_argument(arguments, "include_archived", default=False),
        )
    if tool_name == "get_semantic_memory":
        return service.memory.get_semantic_memory(
            context,
            access_scopes=read_scopes,
            memory_entry_id=uuid_argument(arguments, "memory_entry_id"),
            revision=optional_int_argument(arguments, "revision"),
        )
    if tool_name == "list_semantic_memory_revisions":
        return service.memory.list_semantic_memory_revisions(
            context,
            access_scopes=read_scopes,
            memory_entry_id=uuid_argument(arguments, "memory_entry_id"),
            limit=int_argument(arguments, "limit", default=20),
            offset=int_argument(arguments, "offset", default=0),
        )
    if tool_name == "search_knowledge":
        return {
            "items": service.memory.search_workspace_memory(
                context,
                query=str_argument(arguments, "query", default=""),
                limit=int_argument(arguments, "limit", default=5),
                source_types={"knowledge_source"},
                access_scopes=memory_read_scopes(
                    grants, requested_source_types={"knowledge_source"}
                ),
                tool_name=tool_name,
            )
        }
    if tool_name == "list_knowledge":
        return service.knowledge.list_knowledge(
            context,
            scopes=read_scopes,
            limit=int_argument(arguments, "limit", default=20),
            offset=int_argument(arguments, "offset", default=0),
            include_archived=bool_argument(arguments, "include_archived", default=False),
        )
    if tool_name == "get_knowledge":
        return service.knowledge.get_knowledge(
            context,
            scopes=read_scopes,
            source_id=uuid_argument(arguments, "source_id"),
            content_offset=int_argument(arguments, "content_offset", default=0),
            content_limit=int_argument(arguments, "content_limit", default=8000),
        )
    if tool_name == "get_knowledge_ingestion":
        return service.knowledge.get_knowledge_ingestion(
            context,
            scopes=read_scopes,
            source_id=uuid_argument(arguments, "source_id"),
            ingestion_id=uuid_argument(arguments, "ingestion_id"),
        )
    write_scopes = memory_write_scopes(grants)
    if tool_name == "create_knowledge":
        return service.knowledge.create_knowledge(
            context,
            scopes=write_scopes,
            allowed_file_ids=allowed_file_ids,
            name=str_argument(arguments, "name", default=""),
            description=str_argument(arguments, "description", default=""),
            content=optional_str_argument(arguments, "content"),
            workspace_file_id=optional_uuid_argument(arguments, "workspace_file_id"),
        )
    if tool_name == "update_knowledge":
        return service.knowledge.update_knowledge(
            context,
            scopes=write_scopes,
            allowed_file_ids=allowed_file_ids,
            source_id=uuid_argument(arguments, "source_id"),
            expected_version=int_argument(arguments, "expected_version", default=0),
            name=optional_str_argument(arguments, "name"),
            description=optional_str_argument(arguments, "description"),
            content=optional_str_argument(arguments, "content"),
            workspace_file_id=optional_uuid_argument(arguments, "workspace_file_id"),
        )
    if tool_name == "archive_knowledge":
        return service.knowledge.archive_knowledge(
            context,
            scopes=write_scopes,
            source_id=uuid_argument(arguments, "source_id"),
            expected_version=int_argument(arguments, "expected_version", default=0),
        )
    raise ValueError(f"Unsupported resource tool: {tool_name}")
