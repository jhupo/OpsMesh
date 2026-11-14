"""Short claims, renewable leases, and guarded all-chunk publication."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, or_, select, update

from opsmesh.plugins.invocation import plugin_call

from .errors import InvalidDocument, ModelConfigurationError
from .models import ChunkRecord, DocumentRecord, KnowledgeBaseRecord, now

logger = logging.getLogger(__name__)


async def database_now(session) -> datetime:
    """Use one authoritative clock for cross-process lease and retry rules."""
    return await session.scalar(select(func.clock_timestamp()))


@dataclass(frozen=True)
class IndexClaim:
    """Immutable source and target identity captured by a short claim."""

    document_id: UUID
    knowledge_base_id: UUID
    index_key: UUID
    index_epoch: UUID
    claim_id: UUID
    text: str
    profile: dict[str, Any]


def claim_statement(profile: dict, timestamp):
    """Lock only a document; publication later locks library before document."""
    eligible = or_(
        and_(
            DocumentRecord.status == "pending",
            or_(
                DocumentRecord.retry_at.is_(None),
                DocumentRecord.retry_at <= timestamp,
            ),
        ),
        and_(
            DocumentRecord.status == "indexing",
            DocumentRecord.lease_until <= timestamp,
        ),
    )
    return (
        select(DocumentRecord)
        .join(KnowledgeBaseRecord)
        .where(
            eligible,
            KnowledgeBaseRecord.target_profile == profile,
            DocumentRecord.index_epoch == KnowledgeBaseRecord.index_epoch,
        )
        .order_by(DocumentRecord.created_at, DocumentRecord.id)
        .with_for_update(of=DocumentRecord, skip_locked=True)
        .limit(1)
    )


def claim_matches(
    base: KnowledgeBaseRecord, document: DocumentRecord, claim: IndexClaim
) -> bool:
    """Reject changed input, rebuilds, and reassigned document claims."""
    return (
        base.index_epoch == claim.index_epoch
        and base.target_profile == claim.profile
        and document.index_epoch == claim.index_epoch
        and document.index_key == claim.index_key
        and document.claim_id == claim.claim_id
        and document.status == "indexing"
        and document.lease_until is not None
    )


async def settle_base(session, base: KnowledgeBaseRecord) -> None:
    """Called under the library lock after every completion or deletion."""
    if base.index_status == "ready":
        return
    fingerprint = base.target_profile["fingerprint"]
    failed = await session.scalar(
        select(func.count())
        .select_from(DocumentRecord)
        .where(
            DocumentRecord.knowledge_base_id == base.id,
            DocumentRecord.status == "failed",
        )
    )
    if failed:
        base.index_status = "failed"
        base.updated_at = now()
        return
    unfinished = await session.scalar(
        select(func.count())
        .select_from(DocumentRecord)
        .where(
            DocumentRecord.knowledge_base_id == base.id,
            or_(
                DocumentRecord.status != "ready",
                DocumentRecord.index_epoch != base.index_epoch,
                DocumentRecord.indexed_profile.is_distinct_from(fingerprint),
            ),
        )
    )
    if not unfinished:
        base.active_profile = base.target_profile.copy()
        base.index_status = "ready"
        base.updated_at = now()


class IndexWorker:
    """A recoverable database-backed document loop, not a general job queue."""

    def __init__(self, runtime):
        """Bind one worker to its owning resource activation."""
        self.runtime = runtime
        self.stopping = False
        self.wake = asyncio.Event()

    async def claim(self) -> IndexClaim | None:
        """Claim pending or expired work without holding a transaction open."""
        settings = self.runtime.settings
        async with self.runtime.database.transaction() as session:
            timestamp = await database_now(session)
            document = await session.scalar(
                claim_statement(self.runtime.profile, timestamp)
            )
            if document is None:
                return None
            if document.attempts >= settings.max_attempts:
                # A crashed final attempt needs an explicit retry.
                document.status = "failed"
                document.error = (
                    "Indexing lease expired after the final attempt"
                )
                document.claim_id = None
                document.lease_until = None
                return None
            document.status = "indexing"
            document.claim_id = uuid4()
            document.lease_until = timestamp + timedelta(
                seconds=settings.lease_seconds
            )
            document.attempts += 1
            document.retry_at = None
            document.error = None
            document.updated_at = timestamp
            return IndexClaim(
                document.id,
                document.knowledge_base_id,
                document.index_key,
                document.index_epoch,
                document.claim_id,
                document.text,
                self.runtime.profile,
            )

    async def renew(self, claim: IndexClaim) -> bool:
        """Extend only a still-current, unexpired document claim."""
        async with self.runtime.database.transaction() as session:
            timestamp = await database_now(session)
            result = await session.execute(
                update(DocumentRecord)
                .where(
                    DocumentRecord.id == claim.document_id,
                    DocumentRecord.index_key == claim.index_key,
                    DocumentRecord.index_epoch == claim.index_epoch,
                    DocumentRecord.claim_id == claim.claim_id,
                    DocumentRecord.status == "indexing",
                    DocumentRecord.lease_until > timestamp,
                )
                .values(
                    lease_until=timestamp
                    + timedelta(seconds=self.runtime.settings.lease_seconds)
                )
            )
            return result.rowcount == 1

    async def _locked_records(self, session, claim: IndexClaim):
        base = await session.scalar(
            select(KnowledgeBaseRecord)
            .where(KnowledgeBaseRecord.id == claim.knowledge_base_id)
            .with_for_update()
        )
        if base is None:
            return None, None
        document = await session.scalar(
            select(DocumentRecord)
            .where(
                DocumentRecord.id == claim.document_id,
                DocumentRecord.knowledge_base_id == base.id,
            )
            .with_for_update()
        )
        if document is not None and (
            document.lease_until is None
            or document.lease_until <= await database_now(session)
        ):
            document = None
        return base, document

    async def publish(self, claim: IndexClaim, result) -> bool:
        """Replace every chunk atomically after checking all claim guards."""
        chunks, vectors = result
        async with self.runtime.database.transaction() as session:
            base, document = await self._locked_records(session, claim)
            if document is None or not claim_matches(base, document, claim):
                return False
            await session.execute(
                delete(ChunkRecord).where(
                    ChunkRecord.document_id == document.id
                )
            )
            session.add_all(
                [
                    ChunkRecord(
                        document_id=document.id,
                        index_key=claim.index_key,
                        embedding=vector,
                        **chunk.model_dump(),
                    )
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ]
            )
            document.status = "ready"
            document.indexed_profile = claim.profile["fingerprint"]
            document.claim_id = None
            document.lease_until = None
            document.updated_at = now()
            await session.flush()
            await settle_base(session, base)
            return True

    async def fail(self, claim: IndexClaim, error: Exception) -> None:
        """Retry temporary faults within bounds and record safe diagnostics."""
        async with self.runtime.database.transaction() as session:
            base, document = await self._locked_records(session, claim)
            if document is None or not claim_matches(base, document, claim):
                return
            terminal = (
                isinstance(error, (InvalidDocument, ModelConfigurationError))
                or document.attempts >= self.runtime.settings.max_attempts
            )
            timestamp = await database_now(session)
            document.status = "failed" if terminal else "pending"
            document.error = (
                "Invalid document or encoder configuration"
                if isinstance(error, (InvalidDocument, ModelConfigurationError))
                else "Indexing temporarily unavailable"
            )
            document.retry_at = (
                None
                if terminal
                else timestamp
                + timedelta(
                    seconds=self.runtime.settings.retry_seconds
                    * 2 ** (document.attempts - 1)
                )
            )
            document.claim_id = None
            document.lease_until = None
            document.updated_at = now()
            await session.flush()
            await settle_base(session, base)

    async def process(self, claim: IndexClaim) -> None:
        """Renew the lease while encoding outside database transactions."""
        finished = asyncio.Event()
        lost = False

        async def heartbeat():
            nonlocal lost
            while not finished.is_set():
                try:
                    await asyncio.wait_for(
                        finished.wait(), self.runtime.settings.lease_seconds / 3
                    )
                except TimeoutError:
                    try:
                        if not await self.renew(claim):
                            lost = True
                            return
                    except Exception as error:
                        lost = True
                        logger.warning(
                            "Knowledge index lease renewal failed (%s)",
                            type(error).__name__,
                        )
                        return

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            result = await self.runtime.encoder.documents(claim.text)
            if not lost:
                await self.publish(claim, result)
        except Exception as error:
            await self.fail(claim, error)
        finally:
            finished.set()
            await heartbeat_task

    async def recover_bases(self) -> None:
        """Finish terminal state reconciliation left by expired final claims."""
        async with self.runtime.database.transaction() as session:
            bases = await session.scalars(
                select(KnowledgeBaseRecord)
                .where(
                    KnowledgeBaseRecord.index_status.in_(
                        ("rebuilding", "failed")
                    ),
                    KnowledgeBaseRecord.target_profile == self.runtime.profile,
                )
                .with_for_update(skip_locked=True)
            )
            for base in bases:
                await settle_base(session, base)

    async def run(self) -> None:
        """Recover persisted work until input admission is closed."""
        while not self.stopping:
            try:
                async with plugin_call(self.runtime.owner.id_):
                    claim = await self.claim()
                    if claim is not None:
                        await self.process(claim)
                        continue
                    await self.recover_bases()
            except Exception as error:
                logger.warning(
                    "Knowledge indexing resource failure; retrying (%s)",
                    type(error).__name__,
                )
            self.wake.clear()
            if self.stopping:
                break
            try:
                await asyncio.wait_for(
                    self.wake.wait(), self.runtime.settings.worker_poll_seconds
                )
            except TimeoutError:
                pass
