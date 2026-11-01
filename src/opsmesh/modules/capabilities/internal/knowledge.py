"""Authorized persistent vectors with BM25 lexical and reciprocal-rank hybrid retrieval."""

import hashlib
import math
import re
from collections import Counter
from typing import Literal

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.plugin import OpsMeshPlugin, get_current_plugin
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.capabilities.api import (
    EmbeddingProvider,
    KnowledgeDocument,
    KnowledgeSearchHit,
    ToolAuthorization,
)

from .embeddings import LocalHashEmbedding
from .knowledge_jobs import KnowledgeJobClaims
from .knowledge_management import KnowledgeMaintenance
from .serialization import canonical


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _normalize(vector: tuple[float, ...], dimensions: int) -> tuple[float, ...]:
    if len(vector) != dimensions or any(not math.isfinite(value) for value in vector):
        raise OpsMeshError("EMBEDDING_INVALID", "Embedding dimension or numeric values are invalid")
    length = math.sqrt(sum(value * value for value in vector))
    return tuple(value / length if length else 0.0 for value in vector)


class LocalKnowledgeIndex(KnowledgeMaintenance):
    def __init__(
        self,
        database: AsyncDatabase,
        authorize: ToolAuthorization,
        *,
        embeddings: EmbeddingProvider | None = None,
    ) -> None:
        self._collections = VersionedRecordStore(database, "knowledge_collections")
        self._jobs = VersionedRecordStore(database, "knowledge_maintenance_jobs")
        self._job_claims = KnowledgeJobClaims(database)
        self._documents = VersionedRecordStore(database, "knowledge_documents")
        self._vectors = VersionedRecordStore(database, "knowledge_vectors")
        self._authorize = authorize
        self._providers: dict[str, EmbeddingProvider] = {}
        self._owners: dict[str, OpsMeshPlugin] = {}
        self._default_provider = ""
        self._frozen = False
        self.register_embedding_provider(embeddings or LocalHashEmbedding(), default=True)

    def register_embedding_provider(
        self, provider: EmbeddingProvider, *, default: bool = False
    ) -> None:
        if self._frozen:
            raise OpsMeshError(
                "REGISTRY_FROZEN", "Embedding declarations require a process restart"
            )
        if not provider.id or provider.dimensions < 1 or provider.id in self._providers:
            raise OpsMeshError(
                "CONFIG_INVALID", "Embedding identity must be unique and dimension positive"
            )
        previous = self._default_provider
        self._providers[provider.id] = provider
        if default:
            self._default_provider = provider.id
        if owner := get_current_plugin():
            self._owners[provider.id] = owner

            def rollback() -> None:
                self._providers.pop(provider.id, None)
                self._owners.pop(provider.id, None)
                if self._default_provider == provider.id:
                    self._default_provider = previous

            owner.own(rollback)

    @property
    def embeddings(self) -> EmbeddingProvider:
        owner = self._owners.get(self._default_provider)
        if owner is not None and not owner.enabled:
            raise OpsMeshError("PLUGIN_DISABLED", "Embedding provider plugin is disabled")
        return self._providers[self._default_provider]

    def freeze(self) -> None:
        self._frozen = True

    def _vector_key(self, document: KnowledgeDocument) -> str:
        return hashlib.sha256(
            canonical([self.embeddings.id, self.embeddings.dimensions, document.model_dump()])
        ).hexdigest()

    async def _embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        vectors = await self.embeddings.embed(texts)
        if len(vectors) != len(texts):
            raise OpsMeshError(
                "EMBEDDING_INVALID", "Embedding provider returned the wrong batch size"
            )
        return tuple(_normalize(vector, self.embeddings.dimensions) for vector in vectors)

    async def add(self, document: KnowledgeDocument, *, principal_id: str) -> None:
        await self._authorize(principal_id, f"knowledge:write:{document.collection}")
        await self._ensure_collection(document.collection)
        vector = (await self._embed((document.title + " " + document.text,)))[0]
        await self._store_vector(document, vector)
        await self._documents.create(document.id, document.model_dump(mode="json"))

    async def _store_vector(self, document: KnowledgeDocument, vector: tuple[float, ...]) -> None:
        key = self._vector_key(document)
        try:
            await self._vectors.create(
                key,
                {
                    "values": list(vector),
                    "provider": self.embeddings.id,
                    "document_id": document.id,
                },
            )
        except OpsMeshError as error:
            if error.code != "CONFLICT":
                raise

    async def _document_vectors(
        self, documents: list[KnowledgeDocument]
    ) -> list[tuple[float, ...]]:
        vectors = []
        for document in documents:
            record = await self._vectors.get(self._vector_key(document))
            if record is None:
                vector = (await self._embed((document.title + " " + document.text,)))[0]
                await self._store_vector(document, vector)
            else:
                vector = _normalize(tuple(record.data["values"]), self.embeddings.dimensions)
            vectors.append(vector)
        return vectors

    async def search(
        self,
        collection: str,
        query: str,
        *,
        principal_id: str,
        limit: int = 10,
        mode: Literal["lexical", "vector", "hybrid"] = "hybrid",
    ) -> tuple[KnowledgeSearchHit, ...]:
        await self._authorize(principal_id, f"knowledge:read:{collection}")
        collection_row = await self._collections.get(collection)
        if collection_row is not None and collection_row.data.get("deleted"):
            raise OpsMeshError("NOT_FOUND", "Knowledge collection not found")
        if not 1 <= limit <= 100 or mode not in {"lexical", "vector", "hybrid"}:
            raise OpsMeshError("CONFIG_INVALID", "Invalid search limit or retrieval mode")
        if not query.strip():
            return ()
        documents = [
            KnowledgeDocument.model_validate(record.data)
            for record in await self._documents.list()
            if record.data["collection"] == collection
        ]
        if not documents:
            return ()
        lexical = self._bm25(documents, query) if mode != "vector" else [0.0] * len(documents)
        vector_scores = [0.0] * len(documents)
        if mode != "lexical":
            query_vector = (await self._embed((query,)))[0]
            for index, vector in enumerate(await self._document_vectors(documents)):
                vector_scores[index] = max(
                    0.0, sum(a * b for a, b in zip(query_vector, vector, strict=True))
                )
        scores = lexical if mode == "lexical" else vector_scores
        if mode == "hybrid":
            scores = [0.0] * len(documents)
            for ranking in (lexical, vector_scores):
                indexes = sorted(
                    range(len(documents)), key=lambda i: (-ranking[i], documents[i].id)
                )
                for rank, index in enumerate(indexes, 1):
                    if ranking[index] > 0:
                        scores[index] += 1.0 / (60 + rank)
        hits = [
            KnowledgeSearchHit(
                document=document,
                score=scores[index],
                lexical_score=lexical[index],
                vector_score=vector_scores[index],
            )
            for index, document in enumerate(documents)
            if scores[index] > 0
        ]
        return tuple(sorted(hits, key=lambda hit: (-hit.score, hit.document.id))[:limit])

    @staticmethod
    def _bm25(documents: list[KnowledgeDocument], query: str) -> list[float]:
        tokens = [Counter(_tokens(document.title + " " + document.text)) for document in documents]
        lengths = [sum(token.values()) for token in tokens]
        average = sum(lengths) / len(lengths) or 1
        scores = [0.0] * len(documents)
        for term in set(_tokens(query)):
            frequency = sum(term in token for token in tokens)
            inverse = math.log(1 + (len(documents) - frequency + 0.5) / (frequency + 0.5))
            for index, token in enumerate(tokens):
                count = token[term]
                denominator = count + 1.2 * (0.25 + 0.75 * lengths[index] / average)
                scores[index] += inverse * count * 2.2 / denominator
        return scores
