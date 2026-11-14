"""Detached public values and immutable encoder identity."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .config import EMBEDDING_DIMENSIONS, MODEL_NAME, QUERY_INSTRUCTION


class IndexProfile(BaseModel):
    """Every input that changes the meaning of stored vectors."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    model: str = MODEL_NAME
    revision: str
    manifest_digest: str
    tokenizer_digest: str
    chunk_tokens: int
    overlap_tokens: int
    dimensions: int = EMBEDDING_DIMENSIONS
    normalize: bool = True
    text_normalization: str = "utf8-bom-lf-v1"
    token_limit: int = 512
    query_instruction: str = QUERY_INSTRUCTION

    @property
    def fingerprint(self) -> str:
        """Return the canonical fingerprint used on indexed documents."""
        content = json.dumps(
            self.model_dump(), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(content.encode()).hexdigest()

    def persisted(self) -> dict[str, Any]:
        """Include the fingerprint beside its reproducible inputs."""
        return {**self.model_dump(), "fingerprint": self.fingerprint}


class KnowledgeBaseInfo(BaseModel):
    """Library metadata without a live database session."""

    model_config = ConfigDict(from_attributes=True, frozen=True)
    id: UUID
    owner_plugin: str
    name: str
    description: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    index_status: str
    active_profile: dict[str, Any]
    target_profile: dict[str, Any]
    index_epoch: UUID
    needs_rebuild: bool = False
    created_at: datetime
    updated_at: datetime


class DocumentInfo(BaseModel):
    """Document identity and current indexing state."""

    model_config = ConfigDict(from_attributes=True, frozen=True)
    id: UUID
    knowledge_base_id: UUID
    name: str
    media_type: str
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    status: str
    index_key: UUID
    index_epoch: UUID
    indexed_profile: str | None
    attempts: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class TextChunk(BaseModel):
    """An exact character slice of normalized source text."""

    model_config = ConfigDict(frozen=True)
    ordinal: int
    text: str
    start_offset: int
    end_offset: int
    token_count: int


class DocumentText(BaseModel):
    """A bounded original-text read and its citation identity."""

    model_config = ConfigDict(frozen=True)
    document_id: UUID
    name: str
    text: str
    start_offset: int
    end_offset: int
    total_chars: int
    index_key: UUID


class SearchHit(BaseModel):
    """A scoped source excerpt, not a generated answer."""

    model_config = ConfigDict(frozen=True)
    document_id: UUID
    chunk_id: UUID
    title: str
    text: str
    start_offset: int
    end_offset: int
    distance: float
    index_key: UUID
