import asyncio

from temporalio import activity

from opsmesh.agents.providers.contracts import provider_health_probes
from opsmesh.agents.providers.health import ModelProviderHealthService, ModelProviderHealthSnapshot
from opsmesh.agents.providers.probes import ModelProviderHealthCheckResult, probe_model_provider
from opsmesh.identity.authorization.permissions import WorkspaceAction
from opsmesh.runtime.temporal.activities import ActivityDependencies, authorize_execution
from opsmesh.runtime.temporal.contracts import ExecutionRequest


class ModelProviderHealthActivities:
    def __init__(self, services: ActivityDependencies) -> None:
        self._services = services

    @activity.defn(name="opsmesh.model_provider.check_health")
    async def check_health(self, request: ExecutionRequest) -> None:
        timeout = request.parameters.get("timeout_seconds", 15)
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not 0 < timeout <= 60
        ):
            raise ValueError("Model health timeout must be between 0 and 60 seconds")
        if request.requested_by_user_id is None:
            raise ValueError("Model health check requires its authorized actor")
        snapshot = await asyncio.to_thread(self._prepare, request)
        result = await probe_model_provider(
            snapshot.target,
            probes=provider_health_probes(request.parameters.get("probes")),
            timeout_seconds=float(timeout),
        )
        await asyncio.to_thread(self._complete, request, snapshot, result)

    def _prepare(self, request: ExecutionRequest) -> ModelProviderHealthSnapshot:
        with self._services.transaction() as session:
            authorize_execution(session, request, action=WorkspaceAction.OPERATE)
            return ModelProviderHealthService(
                session, self._services.secrets()
            ).prepare_health_check(
                workspace_id=request.workspace_id,
                credential_id=request.resource_id,
            )

    def _complete(
        self,
        request: ExecutionRequest,
        snapshot: ModelProviderHealthSnapshot,
        result: ModelProviderHealthCheckResult,
    ) -> None:
        if request.requested_by_user_id is None:
            raise ValueError("Model health check requires its authorized actor")
        with self._services.transaction() as session:
            authorize_execution(session, request, action=WorkspaceAction.OPERATE)
            ModelProviderHealthService(session, self._services.secrets()).complete_health_check(
                workspace_id=request.workspace_id,
                credential_id=request.resource_id,
                actor_user_id=request.requested_by_user_id,
                snapshot=snapshot,
                result=result,
            )
