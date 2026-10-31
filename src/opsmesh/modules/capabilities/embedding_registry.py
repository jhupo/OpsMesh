"""Trusted deployment-time embedding registration without data management access."""

from .api import EmbeddingProvider, KnowledgeIndex


class EmbeddingProviderRegistry:
    def __init__(self, index: KnowledgeIndex) -> None:
        self._index = index

    def register_embedding_provider(
        self, provider: EmbeddingProvider, *, default: bool = False
    ) -> None:
        self._index.register_embedding_provider(provider, default=default)
