"""Model provider probe I/O: the narrow boundary that reads the key and calls.

The credential is decrypted inside this activity, at the moment the
connection test needs it, and never leaves it: the key is not logged, not
staged and not part of any payload. A timeout or a transport failure
leaves the outcome unknown or refused with its own reason, because a
request that cannot be confirmed must not be reported as reachable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session
from temporalio import activity

from opsmesh.shared.errors import ForbiddenError, NotFoundError
from opsmesh.shared.security.secrets import SecretCipher
from opsmesh.workspaces.credentials import service as credential_service
from opsmesh.workspaces.model_providers import service
from opsmesh.workspaces.model_providers.models import (
    CONNECTION_STATUS_ACTIVE,
    PROBE_FAILED,
    PROBE_NEEDS_ATTENTION,
    PROBE_OK,
    ModelProviderConnection,
)

PROBE_TIMEOUT_SECONDS = 20.0
MODELS_PATH = "/models"

ProbeFailure = Literal[
    "connection_unavailable",
    "credential_unavailable",
    "credential_payload_invalid",
    "claim_failed",
]


@dataclass(frozen=True)
class ProbeOutcome:
    """What one probe attempt established about its connection."""

    reachable: bool = False
    outcome_unknown: bool = False
    reason: str = ""


@dataclass(frozen=True)
class _Claim:
    """One claimed probe target: the endpoint and the key it needs."""

    url: str
    api_key: str


class ModelProviderActivities:
    """Probe registered connections; the cipher and sessions are injected."""

    def __init__(
        self, cipher: SecretCipher, sessions: async_sessionmaker[AsyncSession]
    ) -> None:
        self._cipher = cipher
        self._sessions = sessions

    @activity.defn
    async def probe_connection(self, connection_id: UUID) -> ProbeOutcome:
        """Call the provider's model list with the stored key and record it."""

        def claim(session: Session) -> tuple[_Claim | None, ProbeFailure | None]:
            connection = session.get(ModelProviderConnection, connection_id)
            if connection is None or connection.status != CONNECTION_STATUS_ACTIVE:
                return None, "connection_unavailable"
            try:
                secret = credential_service.resolve_credential_secret(
                    session,
                    workspace_id=connection.workspace_id,
                    credential_id=connection.credential_id,
                    cipher=self._cipher,
                )
            except (ForbiddenError, NotFoundError):
                return None, "credential_unavailable"
            api_key = secret.get("api_key")
            if not isinstance(api_key, str) or not api_key:
                return None, "credential_payload_invalid"
            base_url = connection.base_url or service.DEFAULT_OPENAI_BASE_URL
            return _Claim(url=base_url.rstrip("/") + MODELS_PATH, api_key=api_key), None

        try:
            async with self._sessions.begin() as session:
                target, failure = await session.run_sync(claim)
        except Exception:
            return await self._record(connection_id, PROBE_FAILED, "claim_failed")
        if target is None:
            return await self._record(connection_id, PROBE_FAILED, failure or "claim_failed")
        try:
            await asyncio.wait_for(
                asyncio.to_thread(_list_models, target.url, target.api_key),
                timeout=PROBE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return await self._record(
                connection_id, PROBE_NEEDS_ATTENTION, "probe_timeout", unknown=True
            )
        except _ProbeTransportError as exc:
            return await self._record(connection_id, PROBE_FAILED, exc.reason)
        return await self._record(connection_id, PROBE_OK, None)

    async def _record(
        self,
        connection_id: UUID,
        status: str,
        detail: str | None,
        *,
        unknown: bool = False,
    ) -> ProbeOutcome:
        def record(session: Session) -> None:
            service.record_probe_outcome(
                session, connection_id=connection_id, status=status, detail=detail
            )

        async with self._sessions.begin() as session:
            await session.run_sync(record)
        return ProbeOutcome(
            reachable=status == PROBE_OK, outcome_unknown=unknown, reason=detail or ""
        )


class _ProbeTransportError(Exception):
    """A confirmed transport failure with a stable reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _list_models(url: str, api_key: str) -> None:
    """Call the provider's model list; only confirmed failures raise."""
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise _ProbeTransportError(f"transport_error:{type(exc).__name__}") from exc
    if response.status_code in {401, 403}:
        raise _ProbeTransportError("unauthorized")
    if response.status_code >= 500:
        raise _ProbeTransportError(f"provider_error:{response.status_code}")
    if response.status_code >= 400:
        raise _ProbeTransportError(f"request_error:{response.status_code}")


__all__ = [
    "PROBE_FAILED",
    "PROBE_NEEDS_ATTENTION",
    "PROBE_OK",
    "ModelProviderActivities",
    "ProbeOutcome",
]
