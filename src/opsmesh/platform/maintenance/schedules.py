from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
    ScheduleUpdate,
    ScheduleUpdateInput,
)

from opsmesh.platform.maintenance.contracts import MaintenanceOperation, PlatformMaintenanceRequest
from opsmesh.shared.config import Settings


async def ensure_platform_schedules(client: Client, settings: Settings) -> None:
    intervals = {
        MaintenanceOperation.HISTORY: settings.platform_history_interval_seconds,
        MaintenanceOperation.AUDIT: settings.audit_integrity_check_interval_seconds,
        MaintenanceOperation.LIFECYCLE: settings.platform_maintenance_interval_seconds,
        MaintenanceOperation.MCP_DIAGNOSTICS: 86400,
    }
    for operation, interval in intervals.items():
        schedule_id = f"opsmesh:platform:maintenance:{operation.value}"
        desired = Schedule(
            action=ScheduleActionStartWorkflow(
                "opsmesh.platform_maintenance",
                PlatformMaintenanceRequest(operation=operation),
                id=schedule_id,
                task_queue=settings.temporal_task_queue,
            ),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(seconds=interval))]),
            policy=SchedulePolicy(
                overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(days=1)
            ),
            state=ScheduleState(
                paused=operation == MaintenanceOperation.HISTORY
                and not settings.platform_history_enabled
            ),
        )
        try:
            await client.create_schedule(schedule_id, desired, rpc_timeout=timedelta(seconds=10))
        except ScheduleAlreadyRunningError:

            def replace(_: ScheduleUpdateInput, desired: Schedule = desired) -> ScheduleUpdate:
                return ScheduleUpdate(schedule=desired)

            await client.get_schedule_handle(schedule_id).update(
                replace, rpc_timeout=timedelta(seconds=10)
            )
