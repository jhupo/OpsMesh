"""The immutable invocation context handed to an extension export.

The context carries the verified subject and scope, the cancellation/deadline
handles, and references to the platform facade and host clients. Extensions
cannot forge any of it: the host builds the context per invocation and the
server re-validates every delegated call.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Final, Protocol, cast, runtime_checkable

from opsmesh_sdk.host.events import EventPublisher
from opsmesh_sdk.host.jobs import JobSubmitter
from opsmesh_sdk.host.models import ModelOperator
from opsmesh_sdk.host.observability import ObservabilityReporter
from opsmesh_sdk.host.secrets import SecretReader
from opsmesh_sdk.host.state import StateStore
from opsmesh_sdk.values import ScopeRef

MAX_NAMESPACE_LENGTH: Final = 100

HostClient = (
    StateStore
    | SecretReader
    | ModelOperator
    | JobSubmitter
    | EventPublisher
    | ObservabilityReporter
)


@runtime_checkable
class PlatformFacade(Protocol):
    """The client facade an extension uses for platform APIs.

    The concrete groups are generated per server step (S4 onward); the facade
    only guarantees attribute access to them.
    """

    def group(self, name: str) -> object: ...


@dataclass(frozen=True)
class InvocationSubject:
    """The verified identity of the caller; never taken from the request body."""

    installation_id: str = field(metadata={"min_length": 1})
    instance_id: str = field(metadata={"min_length": 1})
    invocation_id: str = field(metadata={"min_length": 1})
    namespace: str = field(metadata={"min_length": 1})
    parent_invocation_id: str | None = None


class InvocationContext:
    """Immutable per-invocation context with cancellation and deadline handles."""

    def __init__(
        self,
        subject: InvocationSubject,
        *,
        scope: ScopeRef | None = None,
        platform: PlatformFacade | None = None,
        host_state: StateStore | None = None,
        host_secrets: SecretReader | None = None,
        host_models: ModelOperator | None = None,
        host_jobs: JobSubmitter | None = None,
        host_events: EventPublisher | None = None,
        host_observability: ObservabilityReporter | None = None,
        deadline_seconds: float | None = None,
    ) -> None:
        if not subject.namespace or len(subject.namespace) > MAX_NAMESPACE_LENGTH:
            raise ValueError("a delegated invocation requires its package namespace")
        if deadline_seconds is not None and deadline_seconds <= 0:
            raise ValueError("the deadline must be positive")
        self._subject = subject
        self._scope = scope or ScopeRef()
        self._platform = platform
        self._host: dict[str, HostClient | None] = {
            "state": host_state,
            "secrets": host_secrets,
            "models": host_models,
            "jobs": host_jobs,
            "events": host_events,
            "observability": host_observability,
        }
        self._deadline_seconds = deadline_seconds
        self._cancelled = asyncio.Event()

    @property
    def subject(self) -> InvocationSubject:
        return self._subject

    @property
    def scope(self) -> ScopeRef:
        return self._scope

    @property
    def platform(self) -> PlatformFacade:
        if self._platform is None:
            raise RuntimeError("this context has no platform facade")
        return self._platform

    @property
    def deadline_seconds(self) -> float | None:
        return self._deadline_seconds

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def host(self, group: str) -> HostClient:
        """Access one host group; unbound groups are a configuration error."""
        if group not in self._host:
            raise KeyError(f"unknown host group {group!r}")
        client = self._host[group]
        if client is None:
            raise RuntimeError(f"host group {group!r} is not bound in this context")
        return client

    @property
    def state(self) -> StateStore:
        return cast(StateStore, self.host("state"))

    @property
    def secrets(self) -> SecretReader:
        return cast(SecretReader, self.host("secrets"))

    @property
    def models(self) -> ModelOperator:
        return cast(ModelOperator, self.host("models"))

    @property
    def jobs(self) -> JobSubmitter:
        return cast(JobSubmitter, self.host("jobs"))

    @property
    def events(self) -> EventPublisher:
        return cast(EventPublisher, self.host("events"))

    @property
    def observability(self) -> ObservabilityReporter:
        return cast(ObservabilityReporter, self.host("observability"))

    def cancel(self) -> None:
        """Signal cooperative cancellation to export code."""
        self._cancelled.set()

    async def wait_cancelled(self) -> None:
        await self._cancelled.wait()


__all__ = [
    "MAX_NAMESPACE_LENGTH",
    "InvocationContext",
    "InvocationSubject",
    "PlatformFacade",
]
