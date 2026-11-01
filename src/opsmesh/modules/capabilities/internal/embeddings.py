"""Deterministic CPU feature hashing; external semantic models implement EmbeddingProvider."""

import hashlib
import math
import re


class LocalHashEmbedding:
    """Reproducible word/character similarity, not a pretrained semantic embedding."""

    def __init__(self, dimensions: int = 384) -> None:
        if not 32 <= dimensions <= 8192:
            raise ValueError("Embedding dimensions must be between 32 and 8192")
        self.dimensions = dimensions
        self.id = f"local-hash-v1:{dimensions}"

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector(text) for text in texts)

    def _vector(self, text: str) -> tuple[float, ...]:
        values = [0.0] * self.dimensions
        for word in re.findall(r"\w+", text.casefold()):
            features = [("word:" + word, 2.0)]
            features.extend(("gram:" + word[i : i + 3], 0.5) for i in range(len(word) - 2))
            for feature, weight in features:
                digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
                position = int.from_bytes(digest[:4], "big") % self.dimensions
                values[position] += weight if digest[4] & 1 else -weight
        length = math.sqrt(sum(value * value for value in values))
        return tuple(value / length if length else 0.0 for value in values)
