"""A persistent, atomically updated pool of active backend execution leases.

Construct before database initialization so its table participates in explicit
migrations. All workers share the pool and deployment clocks must be synchronized.
An expired lease is fenced from renewal/release; the execution owner must stop on
renewal failure. A lease does not forcibly terminate a disconnected process.
"""

from __future__ import annotations

import math
from asyncio import sleep
from secrets import compare_digest, token_urlsafe
from time import time
from typing import Literal

from pydantic import Field, ValidationError

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.infrastructure.database import AsyncDatabase, StoredRecord, VersionedRecordStore

_MAX_CAS_ATTEMPTS = 16
_POOL_ID = "backend_execution"


class _AdmissionLease(OpsMeshContract):
    token: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    expires_at: float = Field(gt=0, allow_inf_nan=False, strict=True)


class _AdmissionPool(OpsMeshContract):
    version: Literal[1] = 1
    leases: dict[str, _AdmissionLease] = Field(default_factory=dict)


class AtomicRunAdmission:
    """CAS acquisition counts active leases, including duplicate execution owners.

    The limit applies at each acquisition. Lowering a limit allows existing leases
    to drain and blocks new admission until the active count falls below it. All
    callers must obtain their limit from the same deployment policy.
    """

    def __init__(self, database: AsyncDatabase) -> None:
        self._store = VersionedRecordStore(database, "run_admission_leases")

    @staticmethod
    def _validate_owner(owner: str) -> None:
        if (
            not isinstance(owner, str)
            or not owner
            or len(owner) > 255
            or any(ord(character) < 32 for character in owner)
        ):
            raise OpsMeshError("CONFIG_INVALID", "Admission owner must be a printable identifier")

    @staticmethod
    def _validate_duration(lease_seconds: float) -> None:
        if (
            isinstance(lease_seconds, bool)
            or not math.isfinite(lease_seconds)
            or lease_seconds <= 0
        ):
            raise OpsMeshError(
                "CONFIG_INVALID", "Admission lease duration must be finite and positive"
            )

    @staticmethod
    def _pool(record: StoredRecord | None) -> _AdmissionPool:
        try:
            return _AdmissionPool.model_validate(record.data) if record else _AdmissionPool()
        except ValidationError as error:
            # Corrupt state must never be interpreted as an empty pool.
            raise OpsMeshError(
                "ADMISSION_STATE_INVALID", "Admission pool state is invalid"
            ) from error

    @staticmethod
    def _active(pool: _AdmissionPool, now: float) -> dict[str, _AdmissionLease]:
        return {owner: lease for owner, lease in pool.leases.items() if lease.expires_at > now}

    @staticmethod
    async def _backoff(attempt: int) -> None:
        # Finite, cancellable contention backoff; no synchronous spin loop.
        await sleep(min(0.001 * 2 ** min(attempt, 5), 0.032))

    async def _save(self, record: StoredRecord | None, leases: dict[str, _AdmissionLease]) -> bool:
        data = _AdmissionPool(leases=leases).model_dump(mode="json")
        try:
            if record is None:
                await self._store.create(_POOL_ID, data)
            else:
                await self._store.update(_POOL_ID, data, record.revision)
        except OpsMeshError as error:
            if error.code == "CONFLICT":
                return False
            raise
        return True

    async def acquire(self, owner: str, limit: int, lease_seconds: float = 30) -> str:
        self._validate_owner(owner)
        self._validate_duration(lease_seconds)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise OpsMeshError("CONFIG_INVALID", "Admission limit must be a positive integer")
        token = token_urlsafe(32)
        for attempt in range(_MAX_CAS_ATTEMPTS):
            record = await self._store.get(_POOL_ID)
            now = time()
            leases = self._active(self._pool(record), now)
            # Never hand an existing owner's token to a second executing worker.
            if owner in leases or len(leases) >= limit:
                raise OpsMeshError(
                    "CAPACITY_BUSY", "Backend execution capacity is busy", retryable=True
                )
            leases[owner] = _AdmissionLease(token=token, expires_at=now + lease_seconds)
            if await self._save(record, leases):
                if leases[owner].expires_at <= time():
                    raise OpsMeshError("LEASE_LOST", "Admission lease expired during acquisition")
                return token
            await self._backoff(attempt)
        raise OpsMeshError("CAPACITY_BUSY", "Backend admission is contended", retryable=True)

    @staticmethod
    def _check_token(leases: dict[str, _AdmissionLease], owner: str, token: str) -> _AdmissionLease:
        lease = leases.get(owner)
        if (
            lease is None
            or not isinstance(token, str)
            or not token.isascii()
            or not compare_digest(lease.token, token)
        ):
            raise OpsMeshError("LEASE_LOST", "Admission lease is missing, expired or replaced")
        return lease

    async def renew(self, owner: str, token: str, lease_seconds: float = 30) -> None:
        self._validate_owner(owner)
        self._validate_duration(lease_seconds)
        for attempt in range(_MAX_CAS_ATTEMPTS):
            record = await self._store.get(_POOL_ID)
            now = time()
            leases = self._active(self._pool(record), now)
            lease = self._check_token(leases, owner, token)
            leases[owner] = _AdmissionLease(
                token=token, expires_at=max(lease.expires_at, now + lease_seconds)
            )
            if await self._save(record, leases):
                if leases[owner].expires_at <= time():
                    raise OpsMeshError("LEASE_LOST", "Admission lease expired during renewal")
                return
            await self._backoff(attempt)
        raise OpsMeshError("ADMISSION_CONTENDED", "Admission renewal is contended", retryable=True)

    async def release(self, owner: str, token: str) -> None:
        self._validate_owner(owner)
        for attempt in range(_MAX_CAS_ATTEMPTS):
            record = await self._store.get(_POOL_ID)
            leases = self._active(self._pool(record), time())
            self._check_token(leases, owner, token)
            del leases[owner]
            if await self._save(record, leases):
                return
            await self._backoff(attempt)
        raise OpsMeshError("ADMISSION_CONTENDED", "Admission release is contended", retryable=True)
