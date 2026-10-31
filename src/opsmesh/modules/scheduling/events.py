"""Safe audit facts, committed atomically with schedule configuration and history."""

from typing import Literal

from opsmesh.framework.typing import JsonData

from .api import PersistentSchedule, ScheduleOccurrence


def configuration_event(
    schedule: PersistentSchedule, actor_id: str, action: Literal["created", "updated", "deleted"]
) -> JsonData:
    return {
        "type": f"scheduling.schedule.{action}",
        "source": "scheduling",
        "actor_ref": actor_id,
        "correlation_id": schedule.id,
        "payload": {
            "schedule_id": schedule.id,
            "revision": schedule.revision,
            "principal_id": schedule.principal_id,
            "agent_revision": schedule.agent_revision,
            "enabled": schedule.enabled,
        },
    }


def occurrence_event(occurrence: ScheduleOccurrence, principal_id: str) -> JsonData:
    return {
        "type": f"scheduling.occurrence.{occurrence.status}",
        "source": "scheduling",
        "actor_ref": principal_id,
        "correlation_id": occurrence.schedule_id,
        "causation_id": occurrence.event_id,
        "payload": {
            "occurrence_id": occurrence.id,
            "schedule_revision": occurrence.schedule_revision,
            "run_id": occurrence.run_id,
            "status": occurrence.status,
            "error_code": occurrence.error.code if occurrence.error else None,
        },
    }
