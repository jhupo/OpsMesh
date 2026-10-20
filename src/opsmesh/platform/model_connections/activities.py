"""Read-only upstream probe; only the I/O boundary decrypts the model Key."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from temporalio import activity

from opsmesh.platform.model_connections import service
from opsmesh.platform.model_connections.models import (
    CONNECTION_STATUS_ACTIVE,
    PROBE_FAILED,
    PROBE_NEEDS_ATTENTION,
    PROBE_OK,
    PROBE_RUNNING,
    ModelProviderConnection,
)
from opsmesh.shared.security.encryption import SecretCipher

PROBE_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class ProbeOutcome:
    reachable: bool = False
    outcome_unknown: bool = False
    reason: str = ""


@dataclass(frozen=True)
class _Target:
    url: str
    api_key: str = field(repr=False)


class ModelProviderActivities:
    def __init__(self, cipher: SecretCipher, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._cipher = cipher
        self._sessions = sessions

    @activity.defn
    async def probe_connection(self, command: service.ModelProviderProbeExecution) -> ProbeOutcome:
        def claim(session: Session) -> _Target | None:
            connection = session.get(ModelProviderConnection, command.connection_id)
            if (
                connection is None
                or connection.status != CONNECTION_STATUS_ACTIVE
                or connection.revision != command.revision
                or connection.probe_id != command.execution_id
                or connection.last_probe_status != PROBE_RUNNING
            ):
                return None
            secret = self._cipher.decrypt_payload(
                connection.encrypted_key, key_id=connection.key_id
            )
            api_key = secret.get("api_key")
            if not isinstance(api_key, str) or not api_key:
                raise ValueError("Invalid encrypted model key payload")
            return _Target(connection.base_url.rstrip("/") + "/models", api_key)

        try:
            async with self._sessions.begin() as session:
                target = await session.run_sync(claim)
        except ValueError:
            return await self._record(command, PROBE_FAILED, "key_unavailable")
        if target is None:
            return ProbeOutcome(reason="probe_superseded")
        try:
            async with httpx.AsyncClient(
                timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False
            ) as client:
                response = await client.get(
                    target.url, headers={"Authorization": f"Bearer {target.api_key}"}
                )
            if response.is_success:
                return await self._record(command, PROBE_OK, None)
            reason = "unauthorized" if response.status_code in {401, 403} else "upstream_rejected"
            return await self._record(command, PROBE_FAILED, reason)
        except httpx.TimeoutException:
            return await self._record(command, PROBE_NEEDS_ATTENTION, "probe_timeout")
        except httpx.HTTPError:
            return await self._record(command, PROBE_FAILED, "transport_error")

    async def _record(
        self,
        command: service.ModelProviderProbeExecution,
        status: str,
        detail: str | None,
    ) -> ProbeOutcome:
        async with self._sessions.begin() as session:
            await session.run_sync(
                lambda sync: service.record_probe_outcome(
                    sync,
                    connection_id=command.connection_id,
                    probe_id=command.execution_id,
                    revision=command.revision,
                    status=status,
                    detail=detail,
                )
            )
        return ProbeOutcome(status == PROBE_OK, status == PROBE_NEEDS_ATTENTION, detail or "")
