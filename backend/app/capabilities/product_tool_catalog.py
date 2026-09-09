from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductToolDefinition:
    name: str
    description: str
    input_schema: dict[str, object]
    requires_approval: bool = False
    risk_level: str = "low"
    required_resource_type: str | None = None
    required_access_modes: tuple[str, ...] = ()


def _object_schema(
    properties: dict[str, object] | None = None,
    *,
    required: tuple[str, ...] = (),
) -> dict[str, object]:
    schema: dict[str, object] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


UUID_SCHEMA = {"type": "string", "format": "uuid"}

PRODUCT_TOOL_CATALOG = (
    ProductToolDefinition(
        name="send_agent_message",
        description="Send a workspace-scoped message to another agent profile.",
        input_schema=_object_schema(
            {
                "recipient_agent_profile_id": UUID_SCHEMA,
                "body": {"type": "string", "minLength": 1},
                "thread_id": UUID_SCHEMA,
                "subject": {"type": "string"},
                "message_type": {"type": "string", "default": "message"},
                "payload": {"type": "object"},
                "reply_to_message_id": UUID_SCHEMA,
            },
            required=("recipient_agent_profile_id", "body"),
        ),
    ),
    ProductToolDefinition(
        name="list_agent_thread_messages",
        description="List messages from one workspace-scoped agent thread.",
        input_schema=_object_schema(
            {
                "thread_id": UUID_SCHEMA,
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "status": {"type": "string"},
            },
            required=("thread_id",),
        ),
    ),
    ProductToolDefinition(
        name="get_agent_inbox",
        description="Read the current agent's workspace-scoped inbox summary.",
        input_schema=_object_schema(
            {
                "latest_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                },
                "unread_only": {"type": "boolean", "default": False},
            }
        ),
    ),
    ProductToolDefinition(
        name="mark_agent_message_read",
        description="Mark one workspace-scoped agent message as read.",
        input_schema=_object_schema(
            {"message_id": UUID_SCHEMA},
            required=("message_id",),
        ),
    ),
    ProductToolDefinition(
        name="search_workspace_memory",
        description="Search authorized memory records in the current workspace.",
        input_schema=_object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                "source_types": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            },
            required=("query",),
        ),
        required_resource_type="memory_collection",
        required_access_modes=("read", "read_write"),
    ),
    ProductToolDefinition(
        name="remember_workspace_memory",
        description="Create an authorized durable memory record in the current workspace.",
        input_schema=_object_schema(
            {
                "title": {"type": "string", "default": "Untitled memory"},
                "content": {"type": "string", "minLength": 1},
                "entry_type": {"type": "string", "default": "note"},
                "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "source_type": {"type": "string"},
                "source_id": {"type": "string"},
                "visibility_scope": {"type": "string", "default": "workspace"},
                "importance": {"type": "integer", "minimum": 0, "maximum": 100, "default": 0},
                "metadata": {"type": "object"},
            },
            required=("content",),
        ),
        required_resource_type="memory_collection",
        required_access_modes=("write", "read_write"),
    ),
    ProductToolDefinition(
        name="archive_workspace_memory",
        description="Archive one authorized memory record in the current workspace.",
        input_schema=_object_schema(
            {"memory_entry_id": UUID_SCHEMA},
            required=("memory_entry_id",),
        ),
        requires_approval=True,
        risk_level="medium",
        required_resource_type="memory_collection",
        required_access_modes=("write", "read_write"),
    ),
    ProductToolDefinition(
        name="promote_working_memory",
        description=(
            "Promote one active current-run working-memory record into task-scoped episodic memory."
        ),
        input_schema=_object_schema(
            {"working_memory_entry_id": UUID_SCHEMA},
            required=("working_memory_entry_id",),
        ),
        requires_approval=True,
        risk_level="medium",
        required_resource_type="memory_collection",
        required_access_modes=("write", "read_write"),
    ),
    ProductToolDefinition(
        name="list_workspace_files",
        description="List files visible to the current run in its workspace.",
        input_schema=_object_schema(),
        required_resource_type="file_collection",
        required_access_modes=("read",),
    ),
    ProductToolDefinition(
        name="read_workspace_file",
        description="Read metadata and content for one authorized workspace file.",
        input_schema=_object_schema(
            {"file_id": UUID_SCHEMA},
            required=("file_id",),
        ),
        required_resource_type="file_collection",
        required_access_modes=("read",),
    ),
    ProductToolDefinition(
        name="write_artifact",
        description="Write a run artifact into the current workspace.",
        input_schema=_object_schema(
            {
                "filename": {"type": "string", "minLength": 1, "default": "artifact.txt"},
                "content": {"type": "string"},
                "content_type": {"type": "string", "default": "text/plain"},
                "artifact_type": {"type": "string", "default": "file"},
            },
            required=("content",),
        ),
        requires_approval=True,
        risk_level="medium",
    ),
)

PRODUCT_TOOL_NAMES = frozenset(item.name for item in PRODUCT_TOOL_CATALOG)
