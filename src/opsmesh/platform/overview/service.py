from __future__ import annotations

from sqlalchemy import select

from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.runtime.instances.models import WorkspaceRuntime
from opsmesh.runtime.spaces.models import RuntimeSpace
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.db.read_service import PagedQuerySupport
from opsmesh.workspaces.management.models import Workspace


class AdminOverviewService(PagedQuerySupport):
    def overview(self) -> dict[str, int]:
        return {
            "workspaces_total": self._count(select(Workspace)),
            "workspaces_active": self._count(select(Workspace).where(Workspace.status == "active")),
            "runtime_spaces_total": self._count(select(RuntimeSpace)),
            "runtime_spaces_quarantined": self._count(
                select(RuntimeSpace).where(RuntimeSpace.status == "quarantined")
            ),
            "runtimes_active": self._count(
                select(WorkspaceRuntime).where(WorkspaceRuntime.status == "active")
            ),
            "runtimes_disabled": self._count(
                select(WorkspaceRuntime).where(WorkspaceRuntime.status == "disabled")
            ),
            "temporal_dispatch_pending": self._count(
                select(TemporalOutboxEntry).where(TemporalOutboxEntry.published_at.is_(None))
            ),
            "critical_security_events": self._count(
                select(SecurityEvent).where(SecurityEvent.severity == "critical")
            ),
        }
