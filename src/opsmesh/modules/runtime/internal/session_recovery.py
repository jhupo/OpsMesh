"""Recover logical session ownership after a Run has durably reached its terminal state."""

from collections.abc import Callable

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import StoredRecord, VersionedRecordStore
from opsmesh.modules.runtime.api import TERMINAL_STATES, SessionClaimReader, SessionClaims


class RunSessionRecovery:
    def __init__(
        self,
        runs: VersionedRecordStore,
        claims: VersionedRecordStore,
        claim_key: Callable[[str], str],
        external_claims: SessionClaims | None,
        claim_reader: SessionClaimReader | None,
    ) -> None:
        self.runs, self.claims, self.claim_key = runs, claims, claim_key
        self.external_claims, self.claim_reader = external_claims, claim_reader

    async def release(self, record: StoredRecord) -> None:
        """A delayed cleanup can only release the same Run, never a replacement owner."""
        if self.external_claims is not None:
            await self.external_claims.release(record.data["session_id"], record.id)
            return
        key = self.claim_key(record.data["session_id"])
        for _ in range(8):
            claim = await self.claims.get(key)
            if claim is None or claim.data["run_id"] != record.id:
                return
            try:
                await self.claims.delete(key, claim.revision)
                return
            except OpsMeshError as error:
                if error.code != "CONFLICT":
                    raise
        raise OpsMeshError("CONFLICT", "Session ownership is changing concurrently", retryable=True)

    async def reconcile(self, run_id: str) -> bool:
        record = await self.runs.get(run_id)
        if record is None or record.data["status"] not in TERMINAL_STATES:
            return False
        await self.release(record)
        return True

    async def reconcile_active(self) -> int:
        """Use occupied sessions as the work set, including claims created by late workers."""
        if self.claim_reader is not None:
            selected = await self.claim_reader()
            owners = []
            for session_id, run_id in selected.items():
                record = await self.runs.get(run_id)
                if record is not None and record.data["session_id"] == session_id:
                    owners.append(record)
        elif self.external_claims is None:
            owners = []
            for claim in await self.claims.list():
                record = await self.runs.get(claim.data["run_id"])
                if record is not None and self.claim_key(record.data["session_id"]) == claim.id:
                    owners.append(record)
        else:
            # Compatibility for externally registered claim providers without an inventory API.
            owners = await self.runs.list()
        recovered = 0
        for record in owners:
            if record.data["status"] in TERMINAL_STATES:
                await self.release(record)
                recovered += 1
        return recovered
