"""Public contracts for collection and text ingestion administration."""

from typing import Literal, Self

from pydantic import Field, model_validator

from opsmesh.framework.typing import OpsMeshContract

from .api import KnowledgeDocument


class KnowledgeDocumentRecord(OpsMeshContract):
    document: KnowledgeDocument
    revision: int


class KnowledgeCollection(OpsMeshContract):
    id: str = Field(min_length=1, max_length=100)
    title: str = ""
    description: str = ""
    revision: int = 1
    deleted: bool = False


class KnowledgeTextSource(OpsMeshContract):
    id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=2000000)
    title: str = ""
    format: Literal["text", "markdown"] = "text"


class KnowledgeImportRequest(OpsMeshContract):
    idempotency_key: str = Field(min_length=1, max_length=200)
    sources: tuple[KnowledgeTextSource, ...] = Field(min_length=1, max_length=100)
    chunk_size: int = Field(default=2000, ge=100, le=20000)
    overlap: int = Field(default=200, ge=0, le=5000)

    @model_validator(mode="after")
    def validate_chunks(self) -> Self:
        if self.overlap >= self.chunk_size:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError("Text source identities must be unique within an import")
        return self


class KnowledgeMaintenanceJob(OpsMeshContract):
    id: str
    collection: str
    kind: Literal["import", "rebuild"]
    status: Literal["running", "succeeded", "failed"] = "running"
    total: int = 0
    completed: int = 0
    document_ids: tuple[str, ...] = ()
    error_code: str | None = None
    revision: int = 1
