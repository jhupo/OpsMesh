"""Native Agents SDK tools bound to selected library capabilities."""

from __future__ import annotations

import re

from agents import FunctionTool, function_tool


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
