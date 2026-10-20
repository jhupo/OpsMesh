"""Host base clients: the only interface an extension uses for host services.

Each group is a thin client over the delegation transport. It declares the
current invocation's scope; the host enforces authorization per call. No group
re-implements platform CRUD: user, workspace, capability and file access goes
through ``context.platform``.
"""

from opsmesh_sdk.host.events import EventPublisher
from opsmesh_sdk.host.jobs import JobSubmitter
from opsmesh_sdk.host.models import ModelOperator
from opsmesh_sdk.host.observability import ObservabilityReporter
from opsmesh_sdk.host.state import StateStore

__all__ = [
    "EventPublisher",
    "JobSubmitter",
    "ModelOperator",
    "ObservabilityReporter",
    "StateStore",
]
