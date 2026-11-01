"""File memory contracts, independent of conversation history and checkpoints."""

from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class MemoryPolicy(OpsMeshContract):
    read: bool = True
    generate: bool = True
    live_update: bool = False
    max_sources: int = Field(default=128, gt=0, le=4096)
    timeout_seconds: float = Field(default=120, gt=0)
    extract_model_profile: str | None = None
    consolidate_model_profile: str | None = None
    source_retention_seconds: float | None = Field(default=None, gt=0)
    job_retention_seconds: float | None = Field(default=None, gt=0)


class MemoryNamespace(OpsMeshContract):
    id: str
    owner: str
    readers: tuple[str, ...] = ()
    contributors: tuple[str, ...] = ()
    policy: MemoryPolicy = Field(default_factory=MemoryPolicy)
    revision: int = 1
    memory_revision: int = 0
    artifact_digest: str | None = None
    last_job_id: str | None = None
    deleted: bool = False
    live_updates: dict[str, str] = Field(default_factory=dict)


class MemoryNamespaceSettings(OpsMeshContract):
    readers: tuple[str, ...] = ()
    contributors: tuple[str, ...] = ()
    policy: MemoryPolicy = Field(default_factory=MemoryPolicy)


class MemorySource(OpsMeshContract):
    id: str
    namespace_id: str
    conversation_id: str
    run_id: str
    text: str
    principal_id: str = ""
    extract_model_profile: str | None = None
    consolidate_model_profile: str | None = None


class MemoryExtraction(OpsMeshContract):
    source_id: str
    summary: str
    notes: str
    usage: dict[str, Any] = Field(default_factory=dict)
    principal_id: str = ""
    model_profile: str | None = None
    consolidate_model_profile: str | None = None


class MemoryCandidate(OpsMeshContract):
    summary: str
    index: str
    usage: dict[str, Any] = Field(default_factory=dict)


class MemoryJob(OpsMeshContract):
    id: str
    namespace_id: str
    status: Literal[
        "queued", "extracting", "consolidating", "publishing", "succeeded", "no_change", "failed"
    ] = "queued"
    source_ids: tuple[str, ...]
    base_revision: int
    artifact_digest: str | None = None
    error_code: str | None = None


MemoryExtractor = Callable[[MemorySource], Awaitable[MemoryExtraction]]
MemoryConsolidator = Callable[
    [tuple[MemoryExtraction, ...], MemoryCandidate], Awaitable[MemoryCandidate]
]
MemoryUsageObserver = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class MemoryRetentionResult(OpsMeshContract):
    sources_deleted: int = 0
    jobs_deleted: int = 0
    stage_results_deleted: int = 0


class MemoryService(Protocol):
    async def list_namespaces(self, *, principal_id: str) -> tuple[MemoryNamespace, ...]: ...
    async def configure_namespace(
        self,
        namespace_id: str,
        settings: MemoryNamespaceSettings,
        *,
        principal_id: str,
        expected_revision: int,
    ) -> MemoryNamespace: ...
    async def create_namespace(self, namespace: MemoryNamespace) -> MemoryNamespace: ...
    async def get_namespace(self, namespace_id: str, *, principal_id: str) -> MemoryNamespace: ...
    async def append_source(self, source: MemorySource, *, principal_id: str) -> MemorySource: ...
    async def read(self, namespace_id: str, *, principal_id: str) -> MemoryCandidate: ...
    async def request_generation(
        self, namespace_id: str, *, principal_id: str, idempotency_key: str
    ) -> MemoryJob: ...
    async def run_job(self, job_id: str) -> MemoryJob: ...
    async def get_job(self, job_id: str, *, principal_id: str) -> MemoryJob: ...
    async def list_jobs(self, namespace_id: str, *, principal_id: str) -> tuple[MemoryJob, ...]: ...
    async def list_pending_jobs(self) -> tuple[MemoryJob, ...]: ...
    async def forget(self, namespace_id: str, *, principal_id: str) -> None: ...
    async def update(
        self,
        namespace_id: str,
        candidate: MemoryCandidate,
        *,
        principal_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> MemoryNamespace: ...
    async def prune(self, namespace_id: str, *, principal_id: str) -> MemoryRetentionResult: ...
