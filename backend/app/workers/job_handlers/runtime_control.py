from backend.app.operations.runtime_cleanup import RuntimeCleanupService
from backend.app.operations.worker_lease_maintenance import WorkerLeaseMaintenanceService
from backend.app.runtime_manager.contracts import RuntimeLimits, validate_runtime_execution_mode
from backend.app.runtime_manager.service import RuntimeControlService
from backend.app.workers.job_handlers.context import WorkerJobHandlerContext
from backend.app.workers.job_routing import (
    bool_value,
    optional_uuid,
    positive_float,
    positive_int,
    required_string,
    required_uuid,
    string_list,
)
from backend.app.workers.jobs import JobPayload

RUNTIME_CLEANUP_JOB = "Runtime cleanup job"
RUNTIME_CONTROL_JOB = "Runtime control job"


class RuntimeCleanupJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        routing = dict(job.routing)
        stale_after_seconds = positive_int(
            routing.get("stale_after_seconds"),
            default=600,
            key="stale_after_seconds",
            context=RUNTIME_CLEANUP_JOB,
        )
        stale_lease_after_seconds = positive_int(
            routing.get("stale_lease_after_seconds"),
            default=stale_after_seconds,
            key="stale_lease_after_seconds",
            context=RUNTIME_CLEANUP_JOB,
        )
        RuntimeCleanupService(self._context.session).cleanup_stale_runtimes(
            job.workspace_id,
            stale_after_seconds=stale_after_seconds,
        )
        cleanup = RuntimeCleanupService(self._context.session)
        cleanup.cleanup_terminal_run_workspaces(
            settings=self._context.settings,
            docker_client=self._context.docker_client(),
            workspace_id=job.workspace_id,
            limit=positive_int(
                routing.get("limit"),
                default=100,
                key="limit",
                context=RUNTIME_CLEANUP_JOB,
            ),
        )
        cleanup.cleanup_terminal_run_environments(
            docker_client=self._context.docker_client(),
            workspace_id=job.workspace_id,
            limit=positive_int(
                routing.get("limit"),
                default=100,
                key="limit",
                context=RUNTIME_CLEANUP_JOB,
            ),
        )
        cleanup.cleanup_orphaned_pool_leases(
            docker_client=self._context.docker_client(),
            workspace_id=job.workspace_id,
            stale_after_seconds=stale_after_seconds,
            limit=positive_int(
                routing.get("limit"),
                default=100,
                key="limit",
                context=RUNTIME_CLEANUP_JOB,
            ),
        )
        WorkerLeaseMaintenanceService(self._context.session).expire_stale_worker_leases(
            workspace_id=job.workspace_id,
            stale_after_seconds=stale_lease_after_seconds,
        )


class RuntimeControlJobHandler:
    def __init__(self, context: WorkerJobHandlerContext) -> None:
        self._context = context

    def handle(self, job: JobPayload) -> None:
        settings = self._context.require_settings(context="runtime control")
        action = required_string(job.routing, "action", context=RUNTIME_CONTROL_JOB)
        service = RuntimeControlService(
            self._context.session,
            settings=settings,
            docker_client=self._context.docker_client(),
        )
        match action:
            case "create":
                self._create_runtime(service, job)
            case "start":
                if service.start_runtime(job.workspace_id, job.resource_id) is None:
                    raise ValueError("Runtime not found")
            case "stop":
                if service.stop_runtime(job.workspace_id, job.resource_id) is None:
                    raise ValueError("Runtime not found")
            case "delete":
                if not service.delete_runtime(job.workspace_id, job.resource_id):
                    raise ValueError("Runtime not found")
            case "command":
                self._execute_command(service, job)
            case _:
                raise ValueError(f"Unsupported runtime control action: {action}")

    def _create_runtime(self, service: RuntimeControlService, job: JobPayload) -> None:
        template_id = required_uuid(job.routing, "template_id", context=RUNTIME_CONTROL_JOB)
        name = required_string(job.routing, "name", context=RUNTIME_CONTROL_JOB)
        runtime_space_id = optional_uuid(
            job.routing.get("runtime_space_id"),
            context=RUNTIME_CONTROL_JOB,
        )
        network_disabled = bool_value(
            job.routing.get("network_disabled"),
            True,
            context=RUNTIME_CONTROL_JOB,
        )
        execution_mode_value = required_string(
            job.routing,
            "execution_mode",
            context=RUNTIME_CONTROL_JOB,
        )
        pool_key_value = job.routing.get("pool_key")
        pool_key = pool_key_value if isinstance(pool_key_value, str) else None
        if pool_key_value is not None and pool_key is None:
            raise ValueError("Runtime control job pool_key is invalid")
        if pool_key is not None and (not pool_key.strip() or len(pool_key) > 160):
            raise ValueError("Runtime control job pool_key is invalid")
        execution_mode = validate_runtime_execution_mode(
            execution_mode_value,
            pool_key,
        )
        runtime = service.complete_queued_runtime_create(
            workspace_id=job.workspace_id,
            runtime_id=job.resource_id,
            template_id=template_id,
            name=name,
            limits=_runtime_limits(job.routing.get("limits")),
            runtime_space_id=runtime_space_id,
            network_disabled=network_disabled,
            execution_mode=execution_mode,
            pool_key=pool_key,
        )
        if runtime is None:
            raise ValueError("Queued runtime not found")

    def _execute_command(self, service: RuntimeControlService, job: JobPayload) -> None:
        command = string_list(
            job.routing.get("command"),
            key="command",
            context=RUNTIME_CONTROL_JOB,
        )
        command_id = required_uuid(
            job.routing,
            "runtime_command_id",
            context=RUNTIME_CONTROL_JOB,
        )
        record = service.execute_queued_command(
            workspace_id=job.workspace_id,
            runtime_id=job.resource_id,
            command_id=command_id,
            command=command,
        )
        if record is None:
            raise ValueError("Runtime command not found")


def _runtime_limits(value: object) -> RuntimeLimits | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Runtime control job limits must be an object")
    return RuntimeLimits(
        cpu_count=positive_float(
            value.get("cpu_count"),
            default=1,
            key="cpu_count",
            context=RUNTIME_CONTROL_JOB,
        ),
        memory_mb=positive_int(
            value.get("memory_mb"),
            default=512,
            key="memory_mb",
            context=RUNTIME_CONTROL_JOB,
        ),
        disk_mb=positive_int(
            value.get("disk_mb"),
            default=1024,
            key="disk_mb",
            context=RUNTIME_CONTROL_JOB,
        ),
        timeout_seconds=positive_int(
            value.get("timeout_seconds"),
            default=60,
            key="timeout_seconds",
            context=RUNTIME_CONTROL_JOB,
        ),
        max_output_bytes=positive_int(
            value.get("max_output_bytes"),
            default=256_000,
            key="max_output_bytes",
            context=RUNTIME_CONTROL_JOB,
        ),
        max_processes=positive_int(
            value.get("max_processes"),
            default=256,
            key="max_processes",
            context=RUNTIME_CONTROL_JOB,
        ),
    )
