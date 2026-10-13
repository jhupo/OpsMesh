import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from opsmesh.platform.maintenance.contracts import (
        MaintenanceOperation,
        MaintenanceWorkspacePage,
        PlatformMaintenanceRequest,
        WorkspaceMaintenanceRequest,
    )


@workflow.defn(name="opsmesh.workspace_maintenance")
class WorkspaceMaintenanceWorkflow:
    @workflow.run
    async def run(self, request: WorkspaceMaintenanceRequest) -> None:
        # Lifecycle admission can commit several product intents. A later Schedule
        # occurrence checks persisted due state after an interrupted execution.
        for _ in range(100):
            if await workflow.execute_activity(
                "opsmesh.platform.maintenance_workspace",
                request,
                result_type=bool,
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            ):
                return
            await workflow.sleep(timedelta(seconds=30))
        workflow.continue_as_new(request)


@workflow.defn(name="opsmesh.platform_maintenance")
class PlatformMaintenanceWorkflow:
    @workflow.run
    async def run(self, request: PlatformMaintenanceRequest) -> None:
        if request.operation in {
            MaintenanceOperation.HISTORY,
            MaintenanceOperation.MCP_DIAGNOSTICS,
        }:
            for _ in range(100):
                count = await workflow.execute_activity(
                    "opsmesh.platform.maintenance_global",
                    request,
                    result_type=int,
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                if count == -1:
                    await workflow.sleep(timedelta(seconds=30))
                elif count < 100:
                    return
            workflow.continue_as_new(request)
        for _ in range(20):
            page = await workflow.execute_activity(
                "opsmesh.platform.maintenance_workspaces",
                request,
                result_type=MaintenanceWorkspacePage,
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            for start in range(0, len(page.workspace_ids), 20):
                results = await asyncio.gather(
                    *[
                        workflow.execute_child_workflow(
                            WorkspaceMaintenanceWorkflow.run,
                            WorkspaceMaintenanceRequest(
                                operation=request.operation, workspace_id=identifier
                            ),
                            id=f"{workflow.info().workflow_id}:{identifier}",
                        )
                        for identifier in page.workspace_ids[start : start + 20]
                    ],
                    return_exceptions=True,
                )
                request = request.model_copy(
                    update={
                        "failed_workspaces": request.failed_workspaces
                        + sum(isinstance(result, BaseException) for result in results)
                    }
                )
            if page.next_cursor is None:
                if request.failed_workspaces:
                    raise ApplicationError(
                        f"{request.failed_workspaces} workspace maintenance executions failed",
                        type="workspace_maintenance_failed",
                        non_retryable=True,
                    )
                return
            request = request.model_copy(update={"after_workspace_id": page.next_cursor})
        workflow.continue_as_new(request)
