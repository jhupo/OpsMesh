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
PAGE_PROPERTIES = {
    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
    "offset": {"type": "integer", "minimum": 0, "default": 0},
}
KNOWLEDGE_FIELDS = {
    "name": {"type": "string", "minLength": 1, "maxLength": 160},
    "description": {"type": "string", "maxLength": 2000},
    "content": {"type": "string", "minLength": 1, "maxLength": 3000},
    "workspace_file_id": UUID_SCHEMA,
}

PRODUCT_TOOL_CATALOG = (
    ProductToolDefinition(
        name="list_semantic_memory",
        description="List authorized memory heads without content.",
        input_schema=_object_schema(
            {
                **PAGE_PROPERTIES,
                "include_archived": {"type": "boolean", "default": False},
            }
        ),
        required_resource_type="memory_collection",
        required_access_modes=("read", "read_write"),
    ),
    ProductToolDefinition(
        name="get_semantic_memory",
        description="Read an authorized memory head or exact revision.",
        input_schema=_object_schema(
            {
                "memory_entry_id": UUID_SCHEMA,
                "revision": {"type": "integer", "minimum": 1},
            },
            required=("memory_entry_id",),
        ),
        required_resource_type="memory_collection",
        required_access_modes=("read", "read_write"),
    ),
    ProductToolDefinition(
        name="list_semantic_memory_revisions",
        description="Page revision IDs of authorized memory.",
        input_schema=_object_schema(
            {
                **PAGE_PROPERTIES,
                "memory_entry_id": UUID_SCHEMA,
            },
            required=("memory_entry_id",),
        ),
        required_resource_type="memory_collection",
        required_access_modes=("read", "read_write"),
    ),
    ProductToolDefinition(
        name="search_knowledge",
        description="Retrieve relevant authorized knowledge snippets and citations.",
        input_schema=_object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            },
            required=("query",),
        ),
        required_resource_type="memory_collection",
        required_access_modes=("read", "read_write"),
    ),
    ProductToolDefinition(
        name="list_knowledge",
        description="List authorized knowledge sources without content.",
        input_schema=_object_schema(
            {
                **PAGE_PROPERTIES,
                "include_archived": {"type": "boolean", "default": False},
            }
        ),
        required_resource_type="memory_collection",
        required_access_modes=("read", "read_write"),
    ),
    ProductToolDefinition(
        name="get_knowledge",
        description=(
            "Read a source declaration and page its text; use citations for indexed file/URL spans."
        ),
        input_schema=_object_schema(
            {
                "source_id": UUID_SCHEMA,
                "content_offset": {"type": "integer", "minimum": 0, "default": 0},
                "content_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 12000,
                    "default": 8000,
                },
            },
            required=("source_id",),
        ),
        required_resource_type="memory_collection",
        required_access_modes=("read", "read_write"),
    ),
    ProductToolDefinition(
        name="create_knowledge",
        description=(
            "Create text or authorized file knowledge and queue ingestion. "
            "Provide exactly one of content or workspace_file_id. "
            "Ingestion status is not vector readiness."
        ),
        input_schema=_object_schema(KNOWLEDGE_FIELDS, required=("name",)),
        risk_level="medium",
        required_resource_type="memory_collection",
        required_access_modes=("write", "read_write"),
    ),
    ProductToolDefinition(
        name="update_knowledge",
        description=(
            "Update knowledge at an exact version and queue ingestion; "
            "stale chunks stop matching immediately."
        ),
        input_schema=_object_schema(
            {
                **KNOWLEDGE_FIELDS,
                "source_id": UUID_SCHEMA,
                "expected_version": {"type": "integer", "minimum": 1},
            },
            required=("source_id", "expected_version"),
        ),
        risk_level="medium",
        required_resource_type="memory_collection",
        required_access_modes=("write", "read_write"),
    ),
    ProductToolDefinition(
        name="archive_knowledge",
        description=("Archive knowledge at an exact version and invalidate its indexed chunks."),
        input_schema=_object_schema(
            {
                "source_id": UUID_SCHEMA,
                "expected_version": {"type": "integer", "minimum": 1},
            },
            required=("source_id", "expected_version"),
        ),
        risk_level="medium",
        requires_approval=True,
        required_resource_type="memory_collection",
        required_access_modes=("write", "read_write"),
    ),
    ProductToolDefinition(
        name="get_knowledge_ingestion",
        description=(
            "Read an authorized ingestion status and its source version; "
            "embeddings are maintained separately."
        ),
        input_schema=_object_schema(
            {"source_id": UUID_SCHEMA, "ingestion_id": UUID_SCHEMA},
            required=("source_id", "ingestion_id"),
        ),
        required_resource_type="memory_collection",
        required_access_modes=("read", "read_write"),
    ),
    ProductToolDefinition(
        name="discover_conversation_targets",
        description="Discover authorized expert agents and teams before delegating.",
        input_schema=_object_schema(
            {
                "query": {"type": "string", "maxLength": 200},
                "offset": {"type": "integer", "minimum": 0},
            }
        ),
    ),
    ProductToolDefinition(
        name="delegate_conversation_task",
        description=(
            "Submit work to an authorized agent or team. Returns a task ID. "
            "End your run; the platform resumes you with results. Reuse request_key on retries."
        ),
        input_schema=_object_schema(
            {
                "kind": {"type": "string", "enum": ["agent", "team"]},
                "target_id": UUID_SCHEMA,
                "body": {"type": "string", "minLength": 1, "maxLength": 32000},
                "request_key": {"type": "string", "minLength": 1, "maxLength": 100},
            },
            required=("kind", "target_id", "body", "request_key"),
        ),
        risk_level="medium",
    ),
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
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                "source_types": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
            },
            required=("query",),
        ),
        required_resource_type="memory_collection",
        required_access_modes=("read", "read_write"),
    ),
    ProductToolDefinition(
        name="get_knowledge_citations",
        description="Read citation spans for one authorized knowledge-memory entry.",
        input_schema=_object_schema(
            {"memory_entry_id": UUID_SCHEMA},
            required=("memory_entry_id",),
        ),
        required_resource_type="memory_collection",
        required_access_modes=("read", "read_write"),
    ),
    ProductToolDefinition(
        name="upsert_semantic_memory",
        description=(
            "Create or revise authorized workspace, team, or agent knowledge with version history."
        ),
        input_schema=_object_schema(
            {
                "scope_type": {
                    "type": "string",
                    "enum": ["workspace", "team", "agent"],
                    "default": "workspace",
                },
                "scope_id": UUID_SCHEMA,
                "memory_key": {"type": "string", "minLength": 1, "maxLength": 160},
                "knowledge_type": {
                    "type": "string",
                    "enum": ["fact", "configuration", "policy", "procedure"],
                    "default": "fact",
                },
                "title": {"type": "string", "minLength": 1, "maxLength": 240},
                "content": {"type": "string", "minLength": 1},
                "tags": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "importance": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 50,
                },
                "metadata": {"type": "object"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "change_reason": {"type": "string", "maxLength": 1000},
            },
            required=("scope_id", "memory_key", "title", "content"),
        ),
        risk_level="medium",
        required_resource_type="memory_collection",
        required_access_modes=("write", "read_write"),
    ),
    ProductToolDefinition(
        name="archive_semantic_memory",
        description="Archive one authorized semantic-memory head as a new revision.",
        input_schema=_object_schema(
            {
                "memory_entry_id": UUID_SCHEMA,
                "expected_revision": {"type": "integer", "minimum": 1},
                "change_reason": {"type": "string", "maxLength": 1000},
            },
            required=("memory_entry_id", "expected_revision"),
        ),
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
        risk_level="medium",
    ),
)

PRODUCT_TOOL_NAMES = frozenset(item.name for item in PRODUCT_TOOL_CATALOG)
