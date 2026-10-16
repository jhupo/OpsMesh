"""PlatformClient: group creation, identity propagation and lifecycle.

The client owns the transport and the session identity. Business methods live
in the generated ``api`` groups (delivered with their server steps); this
module only creates groups and propagates authentication, so it never
accumulates business logic.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from typing import Final

from opsmesh_sdk.errors import ValidationError
from opsmesh_sdk.transport.contracts import (
    SESSION_HEADER,
    CancellationScope,
    Transport,
    TransportContext,
)
from opsmesh_sdk.transport.http import HttpTransport

API_PACKAGE: Final = "opsmesh_sdk.api"


class PlatformClient:
    """The public client facade external callers use with a login session."""

    def __init__(
        self,
        base_url: str,
        *,
        session_id: str,
        timeout_seconds: float = 30.0,
        max_connections: int = 20,
        transport: Transport | None = None,
    ) -> None:
        if not session_id:
            raise ValidationError("a platform session id is required")
        self._session_id = session_id
        self._owns_transport = transport is None
        self._transport = transport or HttpTransport(
            base_url,
            session_id=session_id,
            timeout_seconds=timeout_seconds,
            max_connections=max_connections,
        )
        self._groups: dict[str, object] = {}

    @property
    def transport(self) -> Transport:
        return self._transport

    def context(self) -> TransportContext:
        """A fresh per-call context carrying this client's session identity."""
        return TransportContext(
            scope=CancellationScope(),
            identity_headers={SESSION_HEADER: self._session_id},
            trace_headers={},
        )

    def group(self, name: str) -> object:
        """Create (once) and return a generated business group client.

        Groups arrive with their server steps; a group that has not been
        generated yet raises instead of silently degrading.
        """
        if name in self._groups:
            return self._groups[name]
        try:
            module = importlib.import_module(f"{API_PACKAGE}.{name}")
            factory = getattr(module, f"create_{name}_client")
        except (ImportError, AttributeError) as exc:
            raise ValidationError(
                f"the {name!r} API group is not part of this SDK build",
                details={"group": name},
            ) from exc
        group = factory(self)
        self._groups[name] = group
        return group

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def __aenter__(self) -> PlatformClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


async def client_session_context(
    client: PlatformClient,
) -> AsyncIterator[TransportContext]:
    """Yield a fresh call context for one request cycle."""
    yield client.context()


__all__ = ["API_PACKAGE", "PlatformClient", "client_session_context"]
