"""A real transactional audit Activity for distributed-context acceptance."""

import asyncio
import logging
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from opsmesh.governance.audit.service import AuditService
    from opsmesh.identity.authorization.permissions import WorkspaceAction
    from opsmesh.runtime.temporal.activities import ActivityDependencies, authorize_execution
    from opsmesh.runtime.temporal.contracts import ExecutionRequest
    from opsmesh.shared.telemetry.request_context import current_evidence_context


class AuditProbeActivities:
    def __init__(self, services: ActivityDependencies) -> None:
        self.services = services

    @activity.defn(name="acceptance.audit_probe")
    async def record(self, request: ExecutionRequest) -> dict[str, str]:
        before = current_evidence_context()
        await asyncio.sleep(0)
        assert current_evidence_context() == before
        with self.services.transaction() as session:
            authorize_execution(session, request, action=WorkspaceAction.READ)
            event = AuditService(session).record_system_action(
                workspace_id=request.workspace_id,
                action="acceptance.activity",
                target_type="workspace",
                target_id=request.workspace_id,
                metadata={"api_key": "sk-audit-secret-canary"},
            )
            logging.getLogger(__name__).warning("Audit probe", extra={"audit_id": str(event.id)})
        return before | {"audit_id": str(event.id)}


@workflow.defn(name="acceptance.audit_chain")
class AuditChainWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> dict[str, str]:
        return await workflow.execute_activity(
            "acceptance.audit_probe",
            request,
            result_type=dict[str, str],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
