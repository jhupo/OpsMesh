"""Owned local-BGE knowledge bases and native Agents SDK tools."""

from pathlib import Path

from opsmesh.plugins import PluginMetadata, require

database = require("database")
require("filesystem")

from . import lifecycle as _lifecycle  # noqa: E402, F401
from .config import Settings  # noqa: E402
from .errors import (  # noqa: E402
    EncodingBusy,
    IndexUnavailable,
    InvalidDocument,
    InvalidQuery,
    KnowledgeClosed,
    KnowledgeError,
    KnowledgeNotFound,
    ModelConfigurationError,
    StaleReference,
)
from .knowledge_bases import KnowledgeBase, create, get, list  # noqa: E402
from .schemas import (  # noqa: E402
    DocumentInfo,
    DocumentText,
    KnowledgeBaseInfo,
    SearchHit,
)
from .tools import unique_tools  # noqa: E402

__plugin_meta__ = PluginMetadata(
    name="knowledge",
    description="Owned PostgreSQL knowledge bases and local BGE retrieval",
    usage="Require knowledge and add a library's native tools to Agent.tools.",
    type="library",
    config=Settings,
    extra={
        "database": database.DatabaseSchema(
            Path(__file__).with_name("migrations"), "knowledge_0001"
        )
    },
)

__all__ = [
    "DocumentInfo",
    "DocumentText",
    "EncodingBusy",
    "IndexUnavailable",
    "InvalidDocument",
    "InvalidQuery",
    "KnowledgeBase",
    "KnowledgeBaseInfo",
    "KnowledgeClosed",
    "KnowledgeError",
    "KnowledgeNotFound",
    "ModelConfigurationError",
    "SearchHit",
    "Settings",
    "StaleReference",
    "create",
    "get",
    "list",
    "unique_tools",
]
