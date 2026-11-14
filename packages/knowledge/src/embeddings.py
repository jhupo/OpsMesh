"""A pinned local BGE encoder with bounded, cancellation-safe threads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import anyio

from opsmesh.utils import run_sync

from .config import (
    EMBEDDING_DIMENSIONS,
    MODEL_NAME,
    MODEL_TOKEN_LIMIT,
    Settings,
)
from .errors import EncodingBusy, KnowledgeClosed, ModelConfigurationError
from .schemas import IndexProfile, TextChunk
from .text import query_text, split_text


def inspect_manifest(directory: Path, revision: str) -> tuple[str, str]:
    """Verify every local weight/tokenizer file against an offline manifest."""
    manifest_path = directory / "opsmesh-model-manifest.json"
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
        files = manifest["files"]
        if (
            manifest["model"] != MODEL_NAME
            or manifest["revision"] != revision
            or not isinstance(files, dict)
            or not files
        ):
            raise ValueError("Wrong revision or empty file manifest")
        expected = set(files)
        actual = {
            item.relative_to(directory).as_posix()
            for item in directory.rglob("*")
            if item.is_file() and item != manifest_path
        }
        if (
            actual != expected
            or "config.json" not in files
            or "tokenizer.json" not in files
        ):
            raise ValueError("Model files do not match the manifest")
        for relative, digest in files.items():
            candidate = directory / relative
            path = candidate.resolve()
            if (
                not path.is_relative_to(directory.resolve())
                or candidate.is_symlink()
            ):
                raise ValueError("Model file escapes its directory")
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Model file checksum mismatch")
        return hashlib.sha256(raw).hexdigest(), files["tokenizer.json"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ModelConfigurationError(
            "A complete verified local BGE model is required"
        ) from error


def load_model(directory: Path, settings: Settings) -> tuple[Any, IndexProfile]:
    """Load only a verified local directory; never resolve a remote model ID."""
    manifest_digest, tokenizer_digest = inspect_manifest(
        directory, settings.model_revision
    )
    try:
        import torch
        from sentence_transformers import SentenceTransformer

        torch.set_num_threads(settings.threads)
        model = SentenceTransformer(
            str(directory),
            device=settings.device,
            local_files_only=True,
            trust_remote_code=False,
        )
        if model.get_sentence_embedding_dimension() != EMBEDDING_DIMENSIONS:
            raise ValueError("BGE output must have 512 dimensions")
        if not model.tokenizer.is_fast:
            raise ValueError("A fast tokenizer with offsets is required")
        model.max_seq_length = MODEL_TOKEN_LIMIT
    except Exception as error:
        raise ModelConfigurationError(
            "Cannot load the pinned local BGE encoder"
        ) from error
    profile = IndexProfile(
        revision=settings.model_revision,
        manifest_digest=manifest_digest,
        tokenizer_digest=tokenizer_digest,
        chunk_tokens=settings.chunk_tokens,
        overlap_tokens=settings.overlap_tokens,
    )
    return model, profile


class Encoder:
    """Keep model resources alive until all native encoding threads finish."""

    def __init__(self, model: Any, profile: IndexProfile, settings: Settings):
        """Bind a verified model and bounded per-activation execution slots."""
        self.model = model
        self.profile = profile
        self.settings = settings
        self._slots = anyio.CapacityLimiter(settings.encoding_slots)
        self._pending = 0
        self._closing = False
        self._idle = asyncio.Event()
        self._idle.set()

    async def _execute(self, operation):
        if self._closing:
            raise KnowledgeClosed("The encoder is stopping")
        if self._pending >= self.settings.max_pending_encodings:
            raise EncodingBusy("The encoder queue is full")
        self._pending += 1
        self._idle.clear()
        try:
            async with self._slots:
                return await run_sync(operation)()
        finally:
            self._pending -= 1
            if not self._pending:
                self._idle.set()

    async def documents(
        self, text: str
    ) -> tuple[list[TextChunk], list[list[float]]]:
        """Split and encode bounded batches outside database transactions."""

        def encode():
            chunks = split_text(text, self.model.tokenizer, self.settings)
            vectors = self.model.encode(
                [chunk.text for chunk in chunks],
                batch_size=self.settings.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            values = [validate_vector(vector) for vector in vectors]
            if len(values) != len(chunks):
                raise ModelConfigurationError(
                    "Encoder returned the wrong batch size"
                )
            return chunks, values

        return await self._execute(encode)

    async def query(self, query: str) -> list[float]:
        """Count the query instruction before asking BGE for one vector."""

        def encode():
            value = query_text(
                query, self.model.tokenizer, self.profile.query_instruction
            )
            vectors = self.model.encode(
                [value],
                batch_size=1,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            if len(vectors) != 1:
                raise ModelConfigurationError(
                    "Encoder returned the wrong query batch size"
                )
            return validate_vector(vectors[0])

        return await self._execute(encode)

    async def shutdown(self) -> None:
        """Close admission and await thread completion before model release."""
        self._closing = True
        await self._idle.wait()
        self.model = None


def validate_vector(vector) -> list[float]:
    """Reject malformed vectors and normalize harmless floating-point drift."""
    values = [float(value) for value in vector]
    if len(values) != EMBEDDING_DIMENSIONS or not all(
        math.isfinite(value) for value in values
    ):
        raise ModelConfigurationError(
            "Encoder returned an invalid 512-dimensional vector"
        )
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        raise ModelConfigurationError("Encoder returned a zero vector")
    return [value / norm for value in values]
