"""UTF-8 validation and tokenizer-based windows with exact offsets."""

from __future__ import annotations

import re
from bisect import bisect_right
from typing import Any

from .config import MODEL_TOKEN_LIMIT, Settings
from .errors import InvalidDocument, InvalidQuery
from .schemas import TextChunk

_BOUNDARY = re.compile(r"\n\s*\n|[\u3002\uff01\uff1f]|[.!?](?:\s+|$)")


def encode_text(text: str, settings: Settings) -> bytes:
    """Validate Python text before allocating its UTF-8 source bytes."""
    if not isinstance(text, str) or len(text) > settings.max_document_bytes:
        raise InvalidDocument("Document content exceeds the text input limit")
    try:
        return text.encode("utf-8")
    except UnicodeError as error:
        raise InvalidDocument(
            "Document text must contain valid Unicode"
        ) from error


def normalize(content: bytes, settings: Settings) -> str:
    """Preserve semantics while normalizing a BOM and line endings."""
    if (
        not isinstance(content, bytes)
        or len(content) > settings.max_document_bytes
    ):
        raise InvalidDocument("Document exceeds the configured byte limit")
    try:
        text = (
            content.decode("utf-8-sig")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
    except UnicodeError as error:
        raise InvalidDocument("Documents must contain valid UTF-8") from error
    if not text.strip() or "\x00" in text:
        raise InvalidDocument("Document must contain nonempty text without NUL")
    return text


def query_text(query: str, tokenizer: Any, instruction: str) -> str:
    """Reject queries whose instruction and special tokens exceed the model."""
    if not isinstance(query, str) or not query.strip() or "\x00" in query:
        raise InvalidQuery("Query must contain nonempty text")
    value = instruction + query.strip()
    encoded = tokenizer(value, add_special_tokens=True, truncation=False)
    if len(encoded["input_ids"]) > MODEL_TOKEN_LIMIT:
        raise InvalidQuery("Query and retrieval instruction exceed 512 tokens")
    return value


def split_text(
    text: str, tokenizer: Any, settings: Settings
) -> list[TextChunk]:
    """Prefer sentence boundaries, falling back to overlapping token windows."""
    encoded = tokenizer(
        text,
        add_special_tokens=False,
        return_offsets_mapping=True,
        truncation=False,
    )
    offsets = [
        (start, end) for start, end in encoded["offset_mapping"] if end > start
    ]
    if not offsets or len(offsets) > settings.max_document_tokens:
        raise InvalidDocument(
            "Document token count is outside configured bounds"
        )
    starts = [start for start, _ in offsets]
    chunks = []
    cursor = 0
    while cursor < len(offsets):
        stop = min(cursor + settings.chunk_tokens, len(offsets))
        start_char = offsets[cursor][0]
        end_char = offsets[stop - 1][1]
        if stop < len(offsets):
            minimum = offsets[cursor + (stop - cursor) // 2][0]
            boundaries = [
                match.end()
                for match in _BOUNDARY.finditer(text, minimum, end_char)
            ]
            if boundaries:
                end_char = boundaries[-1]
                stop = bisect_right(starts, end_char - 1, cursor, stop)
                end_char = min(end_char, offsets[stop - 1][1])
        while stop > cursor:
            fragment = text[start_char:end_char]
            token_count = len(
                tokenizer(fragment, add_special_tokens=False, truncation=False)[
                    "input_ids"
                ]
            )
            with_specials = len(
                tokenizer(fragment, add_special_tokens=True, truncation=False)[
                    "input_ids"
                ]
            )
            if (
                token_count <= settings.chunk_tokens
                and with_specials <= MODEL_TOKEN_LIMIT
            ):
                break
            stop -= 1
            if stop > cursor:
                end_char = offsets[stop - 1][1]
        if stop == cursor:
            raise InvalidDocument(
                "Tokenizer cannot create a bounded source chunk"
            )
        chunks.append(
            TextChunk(
                ordinal=len(chunks),
                text=fragment,
                start_offset=start_char,
                end_offset=end_char,
                token_count=token_count,
            )
        )
        if len(chunks) > settings.max_chunks:
            raise InvalidDocument("Document exceeds the configured chunk limit")
        if stop == len(offsets):
            break
        cursor = max(cursor + 1, stop - settings.overlap_tokens)
    return chunks
