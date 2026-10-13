from sqlalchemy import select
from temporalio import activity

from opsmesh.capabilities.mcp.execution.retention import expire_mcp_diagnostics
from opsmesh.governance.audit.integrity import AuditIntegrityService
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.identity.authorization.service import WorkspaceAuthorizationService
from opsmesh.platform.maintenance.contracts import (
    MaintenanceOperation,
    MaintenanceWorkspacePage,
    PlatformMaintenanceRequest,
    WorkspaceMaintenanceRequest,
)
from opsmesh.platform.operations.history import PlatformHistoryService
from opsmesh.platform.updates.service import maintenance_enabled
from opsmesh.resources.lifecycle.service import WorkspaceDataLifecycleService
from opsmesh.resources.memory.lifecycle import WorkspaceMemoryLifecycleService
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.workspaces.management.models import Workspace


class PlatformMaintenanceActivities:
    def __init__(self, services: ActivityDependencies) -> None:
        self._services = services

    @activity.defn(name="opsmesh.platform.maintenance_global")
    def global_operation(self, request: PlatformMaintenanceRequest) -> int:
        with self._services.transaction() as session:
            if maintenance_enabled(session):
                return -1
            if request.operation == MaintenanceOperation.HISTORY:
                PlatformHistoryService(session).capture(self._services.settings)
                return 0
            if request.operation == MaintenanceOperation.MCP_DIAGNOSTICS:
                return expire_mcp_diagnostics(
                    session, days=self._services.settings.mcp_diagnostic_retention_days
                )
            raise ValueError("Operation requires an explicit workspace reference")

    @activity.defn(name="opsmesh.platform.maintenance_workspaces")
    def workspaces(self, request: PlatformMaintenanceRequest) -> MaintenanceWorkspacePage:
        with self._services.transaction() as session:
            query = select(Workspace.id)
            if request.operation == MaintenanceOperation.LIFECYCLE:
                query = query.where(Workspace.status == "active")
            elif request.operation != MaintenanceOperation.AUDIT:
                raise ValueError("Operation does not require workspace fanout")
            if request.after_workspace_id is not None:
                query = query.where(Workspace.id > request.after_workspace_id)
            ids = list(session.scalars(query.order_by(Workspace.id).limit(101)))
            return MaintenanceWorkspacePage(
                workspace_ids=ids[:100], next_cursor=ids[99] if len(ids) > 100 else None
            )

    @activity.defn(name="opsmesh.platform.maintenance_workspace")
    def workspace_operation(self, request: WorkspaceMaintenanceRequest) -> bool:
        with self._services.transaction() as session:
            if maintenance_enabled(session):
                return False
            query = select(Workspace).where(Workspace.id == request.workspace_id)
            if request.operation == MaintenanceOperation.LIFECYCLE:
                query = query.with_for_update()
            workspace = session.scalar(query)
            if workspace is None:
                return True
            if request.operation == MaintenanceOperation.AUDIT:
                AuditIntegrityService(session).verify_and_record_workspace_integrity(workspace.id)
            elif request.operation == MaintenanceOperation.LIFECYCLE:
                if workspace.status != "active":
                    return True
                WorkspaceAuthorizationService(session).require_workspace(
                    user_id=workspace.owner_user_id,
                    workspace_id=workspace.id,
                    action=WorkspaceAction.ADMIN,
                )
                WorkspaceMemoryLifecycleService(session).run_workspace(workspace_id=workspace.id)
                WorkspaceDataLifecycleService(session).run_scheduled_lifecycle(
                    workspace_id=workspace.id,
                    limit=1,
                )
            else:
                raise ValueError("Unknown workspace maintenance operation")
            return True
