"""Authorize the official plugin's native sandbox Activities when a Worker claims them."""

import asyncio
from contextlib import suppress
from typing import Any

from temporalio import activity
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor

from opsmesh.agents.execution.providers.openai.sandbox_activities import AgentSandboxActivities
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.runtime.sandboxes.registry import native_resource_identity


class SandboxAdmissionInterceptor(Interceptor):
    def __init__(self, admission: AgentSandboxActivities, provider_names: tuple[str, ...]) -> None:
        self._admission = admission
        self._prefixes = tuple(f"{name}-sandbox_" for name in provider_names)

    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _SandboxAdmission(next, self._admission, self._prefixes)


class _SandboxAdmission(ActivityInboundInterceptor):
    def __init__(
        self,
        next: ActivityInboundInterceptor,
        admission: AgentSandboxActivities,
        prefixes: tuple[str, ...],
    ) -> None:
        super().__init__(next)
        self._admission = admission
        self._prefixes = prefixes

    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        info = activity.info()
        if info.activity_type.startswith(self._prefixes):
            # Agent workflows may resume an authorized persisted session. Creating or
            # deleting compute belongs to the explicit product session lifecycle.
            if info.activity_type.endswith("sandbox_client_create"):
                raise ResourceAccessDeniedError()
            state = getattr(input.args[0], "state", None) if input.args else None
            if state is None:
                raise ResourceAccessDeniedError()
            if info.workflow_id is None:
                raise ResourceAccessDeniedError()
            if info.activity_type.endswith(
                ("sandbox_session_stop", "sandbox_session_shutdown", "sandbox_client_delete")
            ):
                # SDK owns transient Runner resources; the product owns this
                # persisted environment. Its cleanup persists state and PTY cleanup,
                # while Runtime deletion remains the explicit compute release.
                await self._admission.checkpoint_native(info.workflow_id, state)
                if info.activity_type.endswith(
                    ("sandbox_session_stop", "sandbox_session_shutdown")
                ):
                    # Delegate native PTY/dependency cleanup and the official
                    # provider's session-cache eviction. Only deletion changes
                    # ownership from transient SDK resources to product sessions.
                    return await self.next.execute_activity(input)
                return None
            authoritative = await asyncio.to_thread(
                self._admission.authorize_native, info.workflow_id, state
            )
            input.args = [
                input.args[0].model_copy(update={"state": authoritative}),
                *input.args[1:],
            ]
            original_state = authoritative.model_copy(deep=True)
            original_resource = native_resource_identity(original_state)

            # The locked plugin's native Activities have no heartbeat. Temporal
            # delivers cancellation via heartbeat responses during long SDK calls.
            async def heartbeat() -> None:
                while True:
                    activity.heartbeat()
                    await asyncio.sleep(5)

            pulse = asyncio.create_task(heartbeat())
            try:
                result = await self.next.execute_activity(input)
                if info.activity_type.endswith("sandbox_client_resume"):
                    try:
                        await asyncio.to_thread(
                            self._admission.record_native_resume,
                            info.workflow_id,
                            original_state,
                            result.state,
                        )
                    except BaseException:
                        if native_resource_identity(result.state) != original_resource:
                            await asyncio.shield(self._admission.release_replacement(result.state))
                        raise
                return result
            finally:
                pulse.cancel()
                with suppress(asyncio.CancelledError):
                    await pulse
        return await self.next.execute_activity(input)
