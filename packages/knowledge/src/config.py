"""Validated bounds for local BGE encoding and PostgreSQL retrieval."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MODEL_REVISION = "7999e1d3359715c523056ef9478215996d62a620"
MODEL_NAME = "BAAI/bge-small-zh-v1.5"
QUERY_INSTRUCTION = (
    "\u4e3a\u8fd9\u4e2a\u53e5\u5b50\u751f\u6210\u8868\u793a"
    "\u4ee5\u7528\u4e8e\u68c0\u7d22\u76f8\u5173\u6587\u7ae0\uff1a"
)
EMBEDDING_DIMENSIONS = 512
MODEL_TOKEN_LIMIT = 512


class Settings(BaseModel):
    """Resource limits read once for each plugin activation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_revision: str = Field(
        default=MODEL_REVISION, pattern=r"^[a-f0-9]{40}$"
    )
    model_path: Path | None = None
    device: Literal["cpu", "cuda", "mps"] = "cpu"
    chunk_tokens: int = Field(default=400, ge=16, le=510)
    overlap_tokens: int = Field(default=64, ge=0, le=256)
    batch_size: int = Field(default=16, ge=1, le=128)
    threads: int = Field(default=1, ge=1, le=16)
    encoding_slots: int = Field(default=1, ge=1, le=4)
    max_pending_encodings: int = Field(default=16, ge=1, le=256)
    max_document_bytes: int = Field(
        default=4 * 1024 * 1024, ge=1, le=64 * 1024 * 1024
    )
    max_document_tokens: int = Field(default=200_000, ge=512, le=2_000_000)
    max_chunks: int = Field(default=4096, ge=1, le=100_000)
    max_metadata_bytes: int = Field(default=16_384, ge=2, le=1_048_576)
    max_page_size: int = Field(default=100, ge=1, le=1000)
    max_read_chars: int = Field(default=16_000, ge=1, le=100_000)
    max_results: int = Field(default=10, ge=1, le=100)
    max_result_chars: int = Field(default=24_000, ge=1, le=200_000)
    search_mode: Literal["exact", "hnsw"] = "exact"
    hnsw_ef_search: int = Field(default=100, ge=1, le=1000)
    hnsw_max_scan_tuples: int = Field(default=20_000, ge=1, le=1_000_000)
    worker_poll_seconds: float = Field(default=1.0, gt=0, le=60)
    lease_seconds: float = Field(default=120, ge=3, le=3600)
    max_attempts: int = Field(default=3, ge=1, le=20)
    retry_seconds: float = Field(default=5, gt=0, le=3600)
    orphan_grace_seconds: float = Field(default=86_400, ge=3600)

    @model_validator(mode="after")
    def coherent_bounds(self):
        """Ensure windows advance and admitted encodings fit their queue."""
        if self.overlap_tokens >= self.chunk_tokens:
            raise ValueError("overlap_tokens must be smaller than chunk_tokens")
        if self.max_pending_encodings < self.encoding_slots:
            raise ValueError("max_pending_encodings must cover encoding_slots")
        return self
