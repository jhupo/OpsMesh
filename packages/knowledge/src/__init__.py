"""Owned local-BGE knowledge bases and native Agents SDK tools."""

from pathlib import Path

from opsmesh import get_driver
from opsmesh.plugin import PluginMetadata, get_plugin_by_module_name, require

database = require("database")
require("filesystem")

from . import lifecycle as _lifecycle  # noqa: E402
from .config import Settings, ToolSettings  # noqa: E402
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
from .tools import build_tools, unique_tools  # noqa: E402

__plugin_meta__ = PluginMetadata(
    name="knowledge",
    description="Owned PostgreSQL knowledge bases and local BGE retrieval",
    usage="Require knowledge and add its native tools to Agent.tools.",
    type="library",
    config=Settings,
    config_file="config.json",
    extra={
        "database": database.DatabaseSchema(
            Path(__file__).with_name("migrations"), "knowledge_0001"
        )
    },
)

if get_plugin_by_module_name(__name__) is not None:
    get_driver().on_startup(_lifecycle.startup)
    get_driver().on_shutdown(_lifecycle.shutdown)

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
    "ToolSettings",
    "build_tools",
    "create",
    "get",
    "list",
    "unique_tools",
]
