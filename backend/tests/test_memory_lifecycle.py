from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import fakeredis
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.types import JSON

from backend.app.core.config import Settings
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.memory.configuration import (
    MemoryConfigurationConflictError,
    MemoryConfigurationUpdate,
    WorkspaceMemoryConfigurationService,
)
from backend.app.memory.content import memory_content_fingerprint
from backend.app.memory.embedding_jobs import WorkspaceMemoryEmbeddingScheduler
from backend.app.memory.embeddings import (
    MemoryEmbeddingResult,
    OpenAIMemoryEmbeddingProvider,
    WorkspaceMemoryEmbeddingProviderResolver,
)
from backend.app.memory.lifecycle import WorkspaceMemoryLifecycleService
from backend.app.memory.models import (
    WorkspaceMemoryEmbeddingEvent,
    WorkspaceMemoryEntry,
    WorkspaceMemoryLifecycleEvent,
    WorkspaceMemoryVersion,
)
from backend.app.memory.policy import (
    HybridMemoryRetrievalPolicy,
    MemoryLifecyclePolicy,
    default_lifecycle_policy,
    default_retrieval_policy,
)
from backend.app.memory.search import (
    HybridMemorySearchBackend,
    MemorySearchDocument,
    MemorySearchHit,
    MemorySearchRequest,
)
from backend.app.memory.semantic import AgentSemanticMemoryService, SemanticMemoryUpsert
from backend.app.model_providers.credential_commands import ModelProviderCredentialCommandService
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.secrets.service import SecretEncryptionService
from backend.app.workers.job_handlers.context import WorkerJobHandlerContext
from backend.app.workers.job_handlers.memory_embedding import MemoryEmbeddingJobHandler
from backend.app.workers.jobs import JobType
from backend.app.workers.queue.redis_queue import RedisQueue
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_hybrid_retrieval_uses_weighted_rrf_and_deduplicates_content() -> None:
    now = datetime(2026, 9, 9, tzinfo=UTC)
    first = _document("First", "a" * 64, now - timedelta(days=20), importance=20)
    second = _document("Second", "b" * 64, now - timedelta(days=1), importance=80)
    duplicate_second = _document(
        "Second duplicate",
        "b" * 64,
        now - timedelta(days=2),
        importance=80,
    )
    full_text = _StaticBackend(
        "postgres_full_text",
        [_hit(first, 0.9, "postgres_full_text"), _hit(second, 0.7, "postgres_full_text")],
    )
    vector = _StaticBackend(
        "postgres_vector",
        [_hit(second, 0.95, "postgres_vector")],
    )
    lexical = _StaticBackend(
        "lexical",
        [_hit(duplicate_second, 15, "lexical")],
    )
    backend = HybridMemorySearchBackend(
        full_text=full_text,
        vector=vector,
        lexical=lexical,
        retrieval_policy=HybridMemoryRetrievalPolicy(
            importance_weight=0,
            recency_weight=0,
        ),
        lifecycle_policy=MemoryLifecyclePolicy(),
        now=now,
    )
    request = MemorySearchRequest(
        workspace_id=uuid4(),
        query="deployment rules",
        limit=5,
        source_types={"workspace_memory"},
        documents=[first, second, duplicate_second],
        query_embedding=[0.1] * 1_536,
        embedding_model="text-embedding-3-small",
    )

    hits = backend.search(request)

    assert [hit.document.metadata["content_fingerprint"] for hit in hits] == [
        "b" * 64,
        "a" * 64,
    ]
    assert hits[0].backend_name == "hybrid_rrf"
    assert hits[0].ranking_details["backend_ranks"] == {
        "postgres_full_text": 2,
        "postgres_vector": 1,
        "lexical": 1,
    }
    assert hits[0].ranking_details["candidate_count"] == 4
    assert hits[0].ranking_details["deduplicated_count"] == 2
    assert all(item.request is not None for item in (full_text, vector, lexical))
    assert full_text.request.limit == 20


def test_embedding_configuration_schedules_and_completes_versioned_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    user, workspace = _seed_workspace(session, "embedding")
    configuration_service = WorkspaceMemoryConfigurationService(session)
    configuration_service.create_default(workspace.id)
    credential = ModelProviderCredentialCommandService(
        session,
        SecretEncryptionService(secret="test-secret", key_id="test"),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Embedding provider",
        provider="openai",
        api_key="sk-test-embedding",
        default_model="gpt-5.5",
        base_url=None,
        is_default=False,
    )
    semantic_command = SemanticMemoryUpsert(
        workspace_id=workspace.id,
        scope_type="workspace",
        scope_id=workspace.id,
        memory_key="runbook",
        knowledge_type="procedure",
        title="Incident runbook",
        content="Restart the unhealthy worker after draining its queue.",
        tags=["incident"],
        importance=85,
        metadata={},
        changed_by_user_id=user.id,
    )
    semantic_service = AgentSemanticMemoryService(session)
    entry = semantic_service.upsert(semantic_command)
    assert entry.embedding_status == "not_applicable"
    configuration = configuration_service.update(
        workspace_id=workspace.id,
        actor_user_id=user.id,
        command=MemoryConfigurationUpdate(
            embedding_enabled=True,
            embedding_credential_id=credential.id,
            embedding_model="text-embedding-3-small",
            embedding_dimensions=1_536,
            retrieval_policy=default_retrieval_policy(),
            lifecycle_policy=default_lifecycle_policy(),
            expected_version=1,
        ),
    )
    session.commit()
    session.refresh(entry)
    assert configuration.version == 2
    assert entry.embedding_status == "pending"
    assert entry.embedding_generation == 1

    queue = _queue()
    scheduled = WorkspaceMemoryEmbeddingScheduler(session).enqueue_pending(
        queue=queue,
        limit=10,
    )
    session.commit()
    job = queue.dequeue()
    assert scheduled.enqueued == 1
    assert job is not None
    assert job.job_type == JobType.MEMORY_EMBED
    assert job.routing == {"embedding_generation": 1}

    monkeypatch.setattr(
        WorkspaceMemoryEmbeddingProviderResolver,
        "resolve",
        lambda *_args, **_kwargs: _FakeEmbeddingProvider(),
    )
    MemoryEmbeddingJobHandler(
        WorkerJobHandlerContext(
            session=session,
            settings=Settings(environment="test"),
        )
    ).handle(job)
    session.refresh(entry)

    assert entry.embedding_status == "ready"
    assert entry.embedding_model == "text-embedding-3-small"
    assert entry.embedding_content_fingerprint == entry.content_fingerprint
    event = session.scalars(select(WorkspaceMemoryEmbeddingEvent)).one()
    assert event.status == "completed"
    assert event.input_tokens == 12

    semantic_service.archive(entry, expected_revision=1, changed_by_user_id=user.id)
    assert entry.embedding is None
    assert entry.embedding_status == "not_applicable"
    reactivated = semantic_service.upsert(replace(semantic_command, expected_revision=2))
    assert reactivated.embedding_status == "pending"
    assert reactivated.embedding_generation == 3

    reactivated.embedding_status = "processing"
    reactivated.embedding_processing_started_at = datetime.now(UTC) - timedelta(hours=1)
    session.commit()
    recovered = WorkspaceMemoryEmbeddingScheduler(session).enqueue_pending(
        queue=queue,
        limit=10,
        stale_after_seconds=60,
    )
    session.commit()
    assert recovered.recovered == 1
    assert recovered.enqueued == 1
    assert reactivated.embedding_status == "queued"
    assert reactivated.embedding_generation == 4
    recovery_event = session.scalars(
        select(WorkspaceMemoryEmbeddingEvent).where(
            WorkspaceMemoryEmbeddingEvent.status == "recovered"
        )
    ).one()
    assert recovery_event.error_code == "embedding_processing_abandoned"

    with pytest.raises(MemoryConfigurationConflictError, match="version is 2"):
        configuration_service.update(
            workspace_id=workspace.id,
            actor_user_id=user.id,
            command=MemoryConfigurationUpdate(
                embedding_enabled=False,
                embedding_credential_id=None,
                embedding_model="text-embedding-3-small",
                embedding_dimensions=1_536,
                retrieval_policy=default_retrieval_policy(),
                lifecycle_policy=default_lifecycle_policy(),
                expected_version=1,
            ),
        )


def test_openai_embedding_adapter_delegates_to_sdk_embeddings_api() -> None:
    calls: list[dict[str, object]] = []

    class Embeddings:
        def create(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.5] * 1_536)],
                usage=SimpleNamespace(prompt_tokens=7),
            )

    class Client:
        embeddings = Embeddings()

    client_options: dict[str, object] = {}

    def client_factory(**kwargs: object) -> object:
        client_options.update(kwargs)
        return Client()

    provider = OpenAIMemoryEmbeddingProvider(
        api_key="sk-test",
        base_url=None,
        model="text-embedding-3-small",
        dimensions=1_536,
        timeout_seconds=12,
        provider_name="openai",
        client_factory=client_factory,  # type: ignore[arg-type]
    )

    result = provider.embed("deployment policy")

    assert result.input_tokens == 7
    assert len(result.vector) == 1_536
    assert client_options["max_retries"] == 2
    assert calls == [
        {
            "model": "text-embedding-3-small",
            "input": ["deployment policy"],
            "dimensions": 1_536,
            "encoding_format": "float",
        }
    ]


def test_lifecycle_archives_promotes_and_records_deterministic_evidence() -> None:
    session = _session()
    user, workspace = _seed_workspace(session, "lifecycle")
    configuration = WorkspaceMemoryConfigurationService(session).create_default(workspace.id)
    configuration.lifecycle_policy = MemoryLifecyclePolicy(
        semantic_archive_after_days=30,
        auto_promote_episodes=True,
        promotion_min_importance=75,
        promotion_min_access_count=3,
    ).model_dump(mode="json")
    now = datetime(2026, 9, 9, tzinfo=UTC)
    old_semantic = AgentSemanticMemoryService(session).upsert(
        SemanticMemoryUpsert(
            workspace_id=workspace.id,
            scope_type="workspace",
            scope_id=workspace.id,
            memory_key="obsolete-policy",
            knowledge_type="policy",
            title="Obsolete policy",
            content="The retired policy is no longer used.",
            tags=["retired"],
            importance=30,
            metadata={},
            changed_by_user_id=user.id,
        )
    )
    old_semantic.updated_at = now - timedelta(days=31)
    expired = _episode(
        workspace.id,
        "expired",
        importance=20,
        access_count=0,
        expires_at=now - timedelta(seconds=1),
    )
    promotable = _episode(
        workspace.id,
        "promotable",
        importance=90,
        access_count=3,
        expires_at=now + timedelta(days=30),
    )
    session.add_all([expired, promotable])
    session.flush()

    summary = WorkspaceMemoryLifecycleService(session).run_due(now=now, limit=10)
    session.commit()

    assert summary.archived_episodes == 1
    assert summary.archived_semantic == 1
    assert summary.promoted_episodes == 1
    assert expired.status == "archived"
    assert old_semantic.status == "archived"
    assert promotable.status == "promoted"
    promoted = session.scalar(
        select(WorkspaceMemoryEntry).where(
            WorkspaceMemoryEntry.workspace_id == workspace.id,
            WorkspaceMemoryEntry.memory_key
            == f"promoted-episode:{promotable.content_fingerprint}",
        )
    )
    assert promoted is not None
    assert promoted.memory_layer == "semantic"
    assert promoted.scope_type == "workspace"
    assert session.query(WorkspaceMemoryLifecycleEvent).count() == 3
    assert session.query(WorkspaceMemoryVersion).count() == 3


class _StaticBackend:
    def __init__(self, backend_name: str, hits: list[MemorySearchHit]) -> None:
        self.backend_name = backend_name
        self._hits = hits
        self.request: MemorySearchRequest | None = None

    def search(self, request: MemorySearchRequest) -> list[MemorySearchHit]:
        self.request = request
        return self._hits[: request.limit]


class _FakeEmbeddingProvider:
    provider_name = "openai"
    model = "text-embedding-3-small"
    dimensions = 1_536

    def embed(self, text: str) -> MemoryEmbeddingResult:
        assert "Incident runbook" in text
        return MemoryEmbeddingResult(vector=[0.25] * self.dimensions, input_tokens=12)


def _document(
    title: str,
    fingerprint: str,
    created_at: datetime,
    *,
    importance: int,
) -> MemorySearchDocument:
    return MemorySearchDocument(
        source_type="workspace_memory",
        source_id=uuid4(),
        title=title,
        text=f"{title} deployment rules",
        created_at=created_at,
        metadata={
            "content_fingerprint": fingerprint,
            "memory_layer": "semantic",
            "importance": importance,
            "updated_at": created_at.isoformat(),
        },
    )


def _hit(document: MemorySearchDocument, score: float, backend: str) -> MemorySearchHit:
    return MemorySearchHit(
        document=document,
        score=score,
        snippet=document.text,
        backend_name=backend,
    )


def _episode(
    workspace_id: UUID,
    key: str,
    *,
    importance: int,
    access_count: int,
    expires_at: datetime,
) -> WorkspaceMemoryEntry:
    title = f"Episode {key}"
    content = f"Observed operational event {key}."
    return WorkspaceMemoryEntry(
        workspace_id=workspace_id,
        source_type="agent_run",
        source_id=str(uuid4()),
        memory_layer="episodic",
        scope_type="workspace",
        scope_id=str(workspace_id),
        memory_key=f"episode:{key}",
        entry_type="agent_run_completed",
        title=title,
        content=content,
        tags=["episode:run"],
        visibility_scope="workspace",
        importance=importance,
        status="active",
        content_fingerprint=memory_content_fingerprint(title, content),
        memory_metadata={},
        access_count=access_count,
        expires_at=expires_at,
    )


def _queue() -> RedisQueue:
    return RedisQueue(
        redis=fakeredis.FakeRedis(decode_responses=True),
        keys=RedisKeyBuilder("opsmesh"),
        queue_name="agent_runs",
        blocking_timeout_seconds=0,
        visibility_timeout_seconds=900,
    )


def _session() -> Session:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = JSON()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(session: Session, slug: str) -> tuple[User, Workspace]:
    user = User(email=f"{slug}@example.com", display_name=slug.title())
    workspace = Workspace(owner=user, name=slug.title(), slug=slug, settings={})
    session.add_all(
        [user, workspace, WorkspaceMember(workspace=workspace, user=user, role="owner")]
    )
    session.commit()
    return user, workspace
