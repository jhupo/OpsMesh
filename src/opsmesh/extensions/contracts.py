"""Explicit cross-owner assembly points; no business owner is imported here."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.extensions.schemas import InstanceConfiguration
from opsmesh.identity.context import CurrentUser

HostRequest = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class InvocationCancelled(asyncio.CancelledError):
    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__("The isolated process stopped with a confirmed result")
        self.result = result


class InstanceReferences(Protocol):
    def validate(
        self,
        session: Session,
        *,
        user: CurrentUser,
        configuration: InstanceConfiguration,
        artifact: str,
    ) -> None: ...


class InvocationSandbox(Protocol):
    async def invoke(
        self,
        *,
        invocation_id: UUID,
        envelope: dict[str, Any],
        host: HostRequest,
    ) -> dict[str, Any]: ...


class HostOperations(Protocol):
    async def request(
        self,
        *,
        invocation_id: UUID,
        operation_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]: ...
