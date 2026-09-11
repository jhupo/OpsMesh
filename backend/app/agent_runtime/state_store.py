from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRuntimeResumeState
from backend.app.runs.models import AgentRunStateSnapshot
from backend.app.secrets.service import SecretEncryptionService

MAX_SERIALIZED_RUN_STATE_BYTES = 8 * 1024 * 1024
RESUMABLE_STATE_STATUSES = frozenset({"paused", "approved", "rejected"})


class AgentRunStateStore:
    def __init__(self, session: Session, secrets: SecretEncryptionService) -> None:
        self._session = session
        self._secrets = secrets

    def save(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        state: AgentRuntimeResumeState,
    ) -> AgentRunStateSnapshot:
        _validate_resume_state(state)
        encrypted = self._secrets.encrypt_payload(
            {"serialized_state": state.serialized_state}
        )
        snapshot = self._snapshot(workspace_id=workspace_id, run_id=run_id)
        if snapshot is None:
            snapshot = AgentRunStateSnapshot(
                workspace_id=workspace_id,
                agent_run_id=run_id,
                provider=state.provider,
                sdk_version=state.sdk_version,
                schema_version=state.schema_version,
                encrypted_state=encrypted.ciphertext,
                state_fingerprint=encrypted.fingerprint,
                encryption_key_id=encrypted.key_id,
                revision=1,
                status="paused",
            )
            self._session.add(snapshot)
        else:
            snapshot.provider = state.provider
            snapshot.sdk_version = state.sdk_version
            snapshot.schema_version = state.schema_version
            snapshot.encrypted_state = encrypted.ciphertext
            snapshot.state_fingerprint = encrypted.fingerprint
            snapshot.encryption_key_id = encrypted.key_id
            snapshot.revision += 1
            snapshot.status = "paused"
        self._session.flush([snapshot])
        return snapshot

    def load(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
    ) -> AgentRuntimeResumeState | None:
        snapshot = self._snapshot(workspace_id=workspace_id, run_id=run_id)
        if snapshot is None or snapshot.status not in RESUMABLE_STATE_STATUSES:
            return None
        payload = self._secrets.decrypt_payload(
            snapshot.encrypted_state,
            key_id=snapshot.encryption_key_id,
        )
        serialized_state = payload.get("serialized_state")
        if not isinstance(serialized_state, str) or not serialized_state:
            raise ValueError("Stored agent run state is invalid")
        return AgentRuntimeResumeState(
            provider=snapshot.provider,
            serialized_state=serialized_state,
            schema_version=snapshot.schema_version,
            sdk_version=snapshot.sdk_version,
        )

    def mark_consumed(self, *, workspace_id: UUID, run_id: UUID) -> None:
        snapshot = self._snapshot(workspace_id=workspace_id, run_id=run_id)
        if snapshot is None:
            return
        snapshot.status = "consumed"
        self._session.flush([snapshot])

    def _snapshot(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
    ) -> AgentRunStateSnapshot | None:
        return self._session.scalar(
            select(AgentRunStateSnapshot).where(
                AgentRunStateSnapshot.workspace_id == workspace_id,
                AgentRunStateSnapshot.agent_run_id == run_id,
            )
        )


def _validate_resume_state(state: AgentRuntimeResumeState) -> None:
    if not state.provider or len(state.provider) > 80:
        raise ValueError("Agent run state provider is invalid")
    size = len(state.serialized_state.encode("utf-8"))
    if size == 0 or size > MAX_SERIALIZED_RUN_STATE_BYTES:
        raise ValueError("Serialized agent run state exceeds the allowed size")
