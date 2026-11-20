"""Native Agents SDK tools bound to a library or a calling plugin."""

from __future__ import annotations

import re
from uuid import UUID

from agents import FunctionTool, function_tool

from .config import ToolSettings
from .errors import KnowledgeError

_APPROVAL_BY_DEFAULT = frozenset(
    {
        "create_knowledge_base",
        "add_document",
        "replace_document",
        "delete_document",
        "reindex_document",
    }
)


def _tool_error(_context, error: Exception) -> str:
    if isinstance(error, KnowledgeError):
        return str(error)
    return "The knowledge operation failed. Check the inputs and current state."


def _prefix(alias: str) -> str:
    if not isinstance(alias, str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]{0,39}", alias
    ):
        raise ValueError(
            "Tool alias must be an ASCII identifier of at most 40 characters"
        )
    return alias + "_"


def unique_tools(*groups: list[FunctionTool]) -> list[FunctionTool]:
    """Combine tool groups at Agent construction and reject duplicate names."""
    tools = [tool for group in groups for tool in group]
    names = [tool.name for tool in tools]
    if len(names) != len(set(names)):
        raise ValueError("Knowledge tool aliases must produce unique names")
    return tools


def build_tools(options: ToolSettings | None = None) -> list[FunctionTool]:
    """Expose the calling plugin's libraries under a fixed approval policy."""
    from .knowledge_bases import (
        KnowledgeBase,
        _create_owned,
        _list_owned,
        caller_owner,
    )
    from .lifecycle import get_runtime

    owner = caller_owner()
    runtime = get_runtime()
    options = runtime.settings.tools if options is None else options
    prefix = _prefix(options.alias)

    def base(knowledge_base_id: UUID) -> KnowledgeBase:
        return KnowledgeBase(runtime, knowledge_base_id, owner)

    async def create_knowledge_base(name: str, description: str = "") -> dict:
        """Create an empty knowledge base and return its knowledge_base_id."""
        library = await _create_owned(
            runtime, owner, name, description=description
        )
        return {"knowledge_base_id": str(library.id)}

    async def list_knowledge_bases(
        limit: int | None = None, offset: int = 0
    ) -> dict:
        """List owned knowledge bases and index status with pagination."""
        bases = await _list_owned(runtime, owner, limit=limit, offset=offset)
        return {
            "knowledge_bases": [item.model_dump(mode="json") for item in bases]
        }

    async def get_knowledge_base(knowledge_base_id: UUID) -> dict:
        """Get metadata and index status for an owned knowledge base by ID."""
        info = await base(knowledge_base_id).info()
        return info.model_dump(mode="json")

    async def search_knowledge(
        knowledge_base_id: UUID, query: str, limit: int | None = None
    ) -> dict:
        """Find relevant excerpts and citation keys in one knowledge base."""
        hits = await base(knowledge_base_id).search(query, limit=limit)
        return {"hits": [hit.model_dump(mode="json") for hit in hits]}

    async def list_documents(
        knowledge_base_id: UUID, limit: int | None = None, offset: int = 0
    ) -> dict:
        """List document metadata and indexing status with pagination."""
        documents = await base(knowledge_base_id).documents.list(
            limit=limit, offset=offset
        )
        return {
            "documents": [item.model_dump(mode="json") for item in documents]
        }

    async def get_document(knowledge_base_id: UUID, document_id: UUID) -> dict:
        """Get one document's metadata and indexing status without its text."""
        info = await base(knowledge_base_id).documents.get(document_id)
        return info.model_dump(mode="json")

    async def read_document(
        knowledge_base_id: UUID,
        document_id: UUID,
        start: int = 0,
        end: int | None = None,
        index_key: UUID | None = None,
    ) -> dict:
        """Read a bounded text range; reject a stale citation key if supplied.

        Args:
            knowledge_base_id: Owned knowledge-base ID.
            document_id: Document ID within that knowledge base.
            start: Inclusive character offset, starting at zero.
            end: Exclusive character offset, or the configured bounded default.
            index_key: Optional citation key from a previous search.
        """
        result = await base(knowledge_base_id).documents.read(
            document_id, start=start, end=end, index_key=index_key
        )
        return result.model_dump(mode="json")

    async def add_document(
        knowledge_base_id: UUID, name: str, text: str
    ) -> dict:
        """Add complete UTF-8 text as a new document for indexing."""
        info = await base(knowledge_base_id).documents.add(text, name=name)
        return info.model_dump(mode="json")

    async def replace_document(
        knowledge_base_id: UUID, document_id: UUID, text: str
    ) -> dict:
        """Replace a document's full text and invalidate older citations."""
        info = await base(knowledge_base_id).documents.update(document_id, text)
        return info.model_dump(mode="json")

    async def delete_document(
        knowledge_base_id: UUID, document_id: UUID
    ) -> dict:
        """Delete a document and remove it from retrieval."""
        await base(knowledge_base_id).documents.delete(document_id)
        return {"deleted": True}

    async def reindex_document(
        knowledge_base_id: UUID, document_id: UUID
    ) -> dict:
        """Submit a document for reindexing and return its status."""
        info = await base(knowledge_base_id).documents.reindex(document_id)
        return info.model_dump(mode="json")

    return [
        function_tool(
            operation,
            name_override=prefix + operation.__name__,
            docstring_style="google",
            failure_error_function=_tool_error,
            needs_approval=options.approval.get(
                operation.__name__, operation.__name__ in _APPROVAL_BY_DEFAULT
            ),
        )
        for operation in (
            create_knowledge_base,
            list_knowledge_bases,
            get_knowledge_base,
            search_knowledge,
            list_documents,
            get_document,
            read_document,
            add_document,
            replace_document,
            delete_document,
            reindex_document,
        )
    ]


def read_tools(knowledge_base, alias: str) -> list[FunctionTool]:
    """Build native tools whose closures retain one authorized library."""
    prefix = _prefix(alias)

    @function_tool(
        name_override=prefix + "search_knowledge", docstring_style="google"
    )
    async def search_knowledge(query: str, limit: int | None = None) -> dict:
        """Find relevant source excerpts in the selected knowledge base.

        Args:
            query: A nonempty natural-language retrieval query.
            limit: Maximum number of source excerpts to return.

        Returns:
            Source excerpts with titles, character offsets, and citation keys.
            Similarity distances are not correctness probabilities.
        """
        hits = await knowledge_base.search(query, limit=limit)
        return {"hits": [hit.model_dump(mode="json") for hit in hits]}

    @function_tool(
        name_override=prefix + "read_document", docstring_style="google"
    )
    async def read_document(
        document_id: str,
        start: int = 0,
        end: int | None = None,
        index_key: str | None = None,
    ) -> dict:
        """Read a bounded range of original normalized document text.

        Args:
            document_id: Document identifier from this knowledge base.
            start: Inclusive character offset, starting at zero.
            end: Exclusive character offset, or the configured bounded default.
            index_key: Optional citation key returned by a previous search.

        Returns:
            Original text, title, range, total length, and current citation key.
            Replaced citations fail instead of reading unrelated new content.
        """
        value = await knowledge_base.documents.read(
            document_id, start=start, end=end, index_key=index_key
        )
        return value.model_dump(mode="json")

    @function_tool(
        name_override=prefix + "list_documents", docstring_style="google"
    )
    async def list_documents(limit: int | None = None, offset: int = 0) -> dict:
        """List documents and their indexing state in this knowledge base.

        Args:
            limit: Bounded number of document records to return.
            offset: Number of document records to skip.

        Returns:
            Document identifiers, titles, and current indexing state.
        """
        values = await knowledge_base.documents.list(limit=limit, offset=offset)
        return {
            "documents": [value.model_dump(mode="json") for value in values]
        }

    return [search_knowledge, read_document, list_documents]


def management_tools(knowledge_base, alias: str) -> list[FunctionTool]:
    """Build separate mutating tools with native approval enabled."""
    prefix = _prefix(alias)

    @function_tool(
        name_override=prefix + "add_document",
        needs_approval=True,
        docstring_style="google",
    )
    async def add_document(name: str, text: str) -> dict:
        """Accept a new text document for asynchronous indexing after approval.

        Args:
            name: Human-readable title, optionally ending in .txt or .md.
            text: Complete UTF-8 text content to index.

        Returns:
            Accepted document metadata. Pending does not mean searchable yet.
        """
        value = await knowledge_base.documents.add(text, name=name)
        return value.model_dump(mode="json")

    @function_tool(
        name_override=prefix + "replace_document",
        needs_approval=True,
        docstring_style="google",
    )
    async def replace_document(document_id: str, text: str) -> dict:
        """Replace text and invalidate old citations after approval.

        Args:
            document_id: Document identifier within this knowledge base.
            text: Complete replacement text.

        Returns:
            Pending document metadata; old content immediately leaves retrieval.
        """
        value = await knowledge_base.documents.update(document_id, text)
        return value.model_dump(mode="json")

    @function_tool(
        name_override=prefix + "delete_document",
        needs_approval=True,
        docstring_style="google",
    )
    async def delete_document(document_id: str) -> dict:
        """Delete one document from retrieval and storage after approval.

        Args:
            document_id: Document identifier within this knowledge base.

        Returns:
            Confirmation that the document is no longer visible to retrieval.
        """
        await knowledge_base.documents.delete(document_id)
        return {"deleted": True}

    @function_tool(
        name_override=prefix + "reindex_document",
        needs_approval=True,
        docstring_style="google",
    )
    async def reindex_document(document_id: str) -> dict:
        """Resubmit a document for indexing after approval.

        Args:
            document_id: Document identifier within this knowledge base.

        Returns:
            Pending metadata with a fresh citation key and retry allowance.
        """
        value = await knowledge_base.documents.reindex(document_id)
        return value.model_dump(mode="json")

    return [add_document, replace_document, delete_document, reindex_document]
