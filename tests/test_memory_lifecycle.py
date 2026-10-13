from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.agents.providers.credentials import ModelProviderCredentialCommandService
from opsmesh.capabilities.tools.workspace_memory import WorkspaceMemorySearchService
from opsmesh.identity.users.models import User
from opsmesh.resources.memory.activities import MemoryActivities
from opsmesh.resources.memory.authorization import AuthorizedMemoryScope
from opsmesh.resources.memory.configuration import (
    MemoryConfigurationConflictError,
    MemoryConfigurationUpdate,
    WorkspaceMemoryConfigurationService,
)
from opsmesh.resources.memory.embedding_service import (
    MemoryEmbeddingResult,
    OpenAIMemoryEmbeddingProvider,
    WorkspaceMemoryEmbeddingProviderResolver,
    WorkspaceMemoryEmbeddingService,
)
from opsmesh.resources.memory.lifecycle import WorkspaceMemoryLifecycleService
from opsmesh.resources.memory.models import (
    WorkspaceMemoryEmbeddingEvent,
    WorkspaceMemoryEntry,
    WorkspaceMemoryLifecycleEvent,
    WorkspaceMemoryVersion,
    memory_content_fingerprint,
)
from opsmesh.resources.memory.policy import (
    HybridMemoryRetrievalPolicy,
    MemoryLifecyclePolicy,
    default_lifecycle_policy,
    default_retrieval_policy,
)
from opsmesh.resources.memory.retrieval_search import (
    HybridMemorySearchBackend,
    MemorySearchDocument,
    MemorySearchHit,
    MemorySearchRequest,
)
from opsmesh.resources.memory.semantic import SemanticMemoryService, SemanticMemoryUpsert
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.workspaces.management.models import Workspace
from opsmesh.workspaces.members.models import WorkspaceMember
from tests.fixtures.database import product_engine
from tests.fixtures.temporal import durable_executions


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
        snippet_length=200,
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


def test_memory_candidates_are_prefiltered_by_each_resource_grant() -> None:
    session = _session()
    _, workspace = _seed_workspace(session, "scope")
    team_a = uuid4()
    team_b = uuid4()
    entries = [
        _scoped_memory(workspace.id, "Allowed A", team_a, "alpha"),
        _scoped_memory(workspace.id, "Denied cross A", team_a, "beta"),
        _scoped_memory(workspace.id, "Allowed B", team_b, "beta"),
        _scoped_memory(workspace.id, "Denied cross B", team_b, "alpha"),
    ]
    session.add_all(entries)
    session.commit()
    backend = _AllCandidatesBackend()

    results = WorkspaceMemorySearchService(session, ranker=backend).search(
        workspace_id=workspace.id,
        query="deployment memory",
        limit=10,
        memory_layers={"semantic"},
        access_scopes=(
            AuthorizedMemoryScope(
                resource_id=uuid4(),
                access_mode="read",
                source_types=frozenset({"semantic_memory"}),
                tags=frozenset({"alpha"}),
                scope_types=frozenset({"team"}),
                scope_ids=frozenset({str(team_a)}),
            ),
            AuthorizedMemoryScope(
                resource_id=uuid4(),
                access_mode="read",
                source_types=frozenset({"semantic_memory"}),
                tags=frozenset({"beta"}),
                scope_types=frozenset({"team"}),
                scope_ids=frozenset({str(team_b)}),
            ),
        ),
    )

    assert backend.request is not None
    assert {document.title for document in backend.request.documents} == {
        "Allowed A",
        "Allowed B",
    }
    assert {item["title"] for item in results} == {"Allowed A", "Allowed B"}


@pytest.mark.parametrize("change_configuration", [False, True])
@pytest.mark.parametrize("interrupted", [False, True])
def test_embedding_configuration_schedules_and_completes_versioned_work(
    monkeypatch: pytest.MonkeyPatch,
    change_configuration: bool,
    interrupted: bool,
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
    semantic_service = SemanticMemoryService(session)
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
    assert entry.embedding_status == "queued"
    assert entry.embedding_generation == 1

    from opsmesh.resources.memory.models import WorkspaceMemoryConfiguration

    engine = session.get_bind()
    sessions = sessionmaker(engine, expire_on_commit=False)
    requests = durable_executions(session, workspace.id, ExecutionKind.MEMORY_EMBED)
    assert len(requests) == 1 and requests[0].parameters == {"embedding_generation": 1}
    workspace_id, user_id = workspace.id, user.id
    session.rollback()
    if interrupted:
        with sessions.begin() as previous_attempt:
            prepared = WorkspaceMemoryEmbeddingService(previous_attempt).prepare(
                workspace_id=workspace_id,
                memory_entry_id=entry.id,
                embedding_generation=1,
            )
            assert prepared is not None
        # A committed prepare survives a Worker exit before the native retry.
        session.refresh(entry)
        assert entry.embedding_status == "processing"
        session.rollback()
    calls = []
    closed = []

    class Provider(_FakeEmbeddingProvider):
        def embed(self, text):
            assert engine.pool.checkedout() == 0
            calls.append(text)
            if change_configuration:
                with sessions.begin() as updating:
                    current = updating.scalar(select(WorkspaceMemoryConfiguration))
                    WorkspaceMemoryConfigurationService(updating).update(
                        workspace_id=workspace_id,
                        actor_user_id=user_id,
                        command=MemoryConfigurationUpdate(
                            embedding_enabled=False,
                            embedding_credential_id=None,
                            embedding_model=current.embedding_model,
                            embedding_dimensions=current.embedding_dimensions,
                            retrieval_policy=current.retrieval_policy,
                            lifecycle_policy=current.lifecycle_policy,
                            expected_version=current.version,
                        ),
                    )
            return super().embed(text)

        def close(self):
            closed.append(True)

    monkeypatch.setattr(
        WorkspaceMemoryEmbeddingProviderResolver, "resolve", lambda *_a, **_kw: Provider()
    )
    activities = MemoryActivities(ActivityDependencies(sessions, Settings(environment="test")))
    activities.embed(requests[0])
    session.refresh(entry)
    if change_configuration:
        assert entry.embedding is None and entry.embedding_status == "not_applicable"
        assert not list(session.scalars(select(WorkspaceMemoryEmbeddingEvent)))
        assert len(calls) == len(closed) == 1
        return
    assert entry.embedding_status == "ready"
    assert entry.embedding_model == "text-embedding-3-small"
    assert entry.embedding_content_fingerprint == entry.content_fingerprint
    event = session.scalars(select(WorkspaceMemoryEmbeddingEvent)).one()
    assert event.status == "completed" and event.input_tokens == 12
    session.rollback()
    activities.embed(requests[0])
    assert len(calls) == len(closed) == 1

    semantic_service.archive(entry, expected_revision=1, changed_by_user_id=user_id)
    assert entry.embedding is None and entry.embedding_status == "not_applicable"
    reactivated = semantic_service.upsert(replace(semantic_command, expected_revision=2))
    assert reactivated.embedding_status == "queued" and reactivated.embedding_generation == 3
    session.commit()
    reactivated.embedding_status = "failed"
    reactivated.embedding_last_error_code = "embedding_provider_timeout"
    session.commit()
    assert (
        configuration_service.retry_failed_embeddings(
            workspace_id=workspace_id, actor_user_id=user_id
        )
        == 1
    )
    session.commit()
    session.refresh(reactivated)
    assert reactivated.embedding_status == "queued" and reactivated.embedding_generation == 4
    requests = durable_executions(session, workspace_id, ExecutionKind.MEMORY_EMBED)
    assert sorted(item.parameters["embedding_generation"] for item in requests) == [1, 3, 4]
    with pytest.raises(MemoryConfigurationConflictError, match="version is 2"):
        configuration_service.update(
            workspace_id=workspace_id,
            actor_user_id=user_id,
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
    old_semantic = SemanticMemoryService(session).upsert(
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
    _, foreign = _seed_workspace(session, "foreign-lifecycle")
    WorkspaceMemoryConfigurationService(session).create_default(foreign.id)
    foreign_episode = _episode(
        foreign.id,
        "foreign-expired",
        importance=20,
        access_count=0,
        expires_at=now - timedelta(seconds=1),
    )
    session.add(foreign_episode)
    session.flush()

    summary = WorkspaceMemoryLifecycleService(session).run_workspace(
        workspace_id=workspace.id, now=now, limit=10
    )
    session.commit()

    assert summary.archived_episodes == 1
    assert summary.archived_semantic == 1
    assert summary.promoted_episodes == 1
    assert expired.status == "archived"
    assert old_semantic.status == "archived"
    assert promotable.status == "promoted"
    assert foreign_episode.status == "active"
    promoted = session.scalar(
        select(WorkspaceMemoryEntry).where(
            WorkspaceMemoryEntry.workspace_id == workspace.id,
            WorkspaceMemoryEntry.memory_key == f"promoted-episode:{promotable.content_fingerprint}",
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


class _AllCandidatesBackend:
    backend_name = "authorization_test"

    def __init__(self) -> None:
        self.request: MemorySearchRequest | None = None

    def search(self, request: MemorySearchRequest) -> list[MemorySearchHit]:
        self.request = request
        return [
            MemorySearchHit(
                document=document,
                score=1,
                snippet=document.text,
                backend_name=self.backend_name,
            )
            for document in request.documents
        ]


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


def _scoped_memory(
    workspace_id: UUID,
    title: str,
    scope_id: UUID,
    tag: str,
) -> WorkspaceMemoryEntry:
    content = f"{title} deployment memory"
    return WorkspaceMemoryEntry(
        workspace_id=workspace_id,
        source_type="semantic_memory",
        source_id=str(uuid4()),
        memory_layer="semantic",
        scope_type="team",
        scope_id=str(scope_id),
        memory_key=title.lower().replace(" ", "-"),
        entry_type="semantic_fact",
        title=title,
        content=content,
        tags=[tag],
        visibility_scope="team",
        importance=50,
        status="active",
        content_fingerprint=memory_content_fingerprint(title, content),
        memory_metadata={},
    )


def _session() -> Session:
    engine = product_engine()
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
