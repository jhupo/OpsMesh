"""Injected service boundaries for application workflows, independent of bootstrap."""

from __future__ import annotations

from typing import Protocol

from opsmesh.framework.adapters import ReplyTarget
from opsmesh.infrastructure.run_workers.status import WorkerProcessStatus
from opsmesh.modules.triggers.api import TriggerService


class ApplicationSettings(Protocol):
    @property
    def poll_interval_seconds(self) -> float: ...
    @property
    def budget_currency(self) -> str: ...
    @property
    def run_reservation_amount(self) -> str: ...
    @property
    def allow_trusted_local(self) -> bool: ...
    @property
    def personal_model_allowed_hosts(self) -> tuple[str, ...]: ...
    @property
    def memory_extract_model_profile(self) -> str | None: ...
    @property
    def memory_consolidate_model_profile(self) -> str | None: ...


class ApplicationTriggerServices(TriggerService, Protocol):
    async def reconcile(self) -> int: ...


class NotificationObserver(Protocol):
    async def observe(
        self,
        *,
        event_id: str,
        event_type: str,
        owner_id: str,
        resource_id: str,
        status: str,
        summary: str = "",
        default_target: ReplyTarget | None = None,
    ) -> None: ...
    async def tick(self) -> None: ...


class RunWorkerController(Protocol):
    async def reap(self) -> None: ...
    async def poll(self, run_id: str) -> WorkerProcessStatus | None: ...
    async def launch(self, run_id: str) -> None: ...
