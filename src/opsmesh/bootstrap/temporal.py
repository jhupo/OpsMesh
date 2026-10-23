"""Compose the native Client, payload codec and Activity execution context."""

import asyncio
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import replace
from datetime import timedelta
from typing import Any, Protocol, runtime_checkable

from agents import ModelProvider
from agents.mcp import MCPServer
from temporalio import activity
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.contrib.opentelemetry import OpenTelemetryPlugin
from temporalio.converter import DataConverter
from temporalio.exceptions import ApplicationError
from temporalio.openai_agents import (
    ModelActivityParameters,
    OpenAIAgentsPlugin,
    SandboxClientProvider,
)
from temporalio.plugin import SimplePlugin
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor
from temporalio.workflow import ActivityCancellationType

from opsmesh.config import Settings
from opsmesh.shared.errors import DomainError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.telemetry.context import bind_log_context
from opsmesh.temporal.codec import EncryptedPayloadCodec
from opsmesh.workflows.engines.temporal.failures import WorkspaceFailureConverter


@runtime_checkable
class LoggableReference(Protocol):
    def log_attributes(self) -> dict[str, object]: ...


class ExecutionContextInterceptor(Interceptor):
    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _ExecutionActivityContext(next)


class _ExecutionActivityContext(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        # Clear inherited business fields before binding this Activity's own references.
        values: dict[str, object] = {}
        info = activity.info()
        values.update(
            workflow_id=info.workflow_id,
            workflow_run_id=info.workflow_run_id,
            activity_id=info.activity_id,
            activity_attempt=info.attempt,
        )
        for argument in input.args:
            if isinstance(argument, LoggableReference):
                values.update(argument.log_attributes())
                break
        with bind_log_context(replace=True, **values):
            heartbeat = None
            if info.heartbeat_timeout is not None:
                heartbeat = asyncio.create_task(
                    self.heartbeat(info.heartbeat_timeout.total_seconds() / 4)
                )
            try:
                return await super().execute_activity(input)
            except DomainError as exc:
                raise ApplicationError(exc.message, type=exc.code, non_retryable=True) from exc
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat

    async def heartbeat(self, interval: float) -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(interval)


async def connect_temporal(
    settings: Settings,
    *,
    model_provider: ModelProvider | None = None,
    mcp_servers: Mapping[str, Callable[..., MCPServer]] | None = None,
    sandbox_clients: tuple[SandboxClientProvider, ...] = (),
) -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        tls=settings.temporal_tls_enabled,
        api_key=settings.temporal_api_key,
        plugins=[
            OpenAIAgentsPlugin(
                model_provider=model_provider,
                mcp_servers=mcp_servers,
                sandbox_clients=sandbox_clients,
                register_activities=model_provider is not None,
                model_params=ModelActivityParameters(
                    start_to_close_timeout=timedelta(minutes=5),
                    heartbeat_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                    cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                ),
            ),
            OpenTelemetryPlugin(add_temporal_spans=True),
            SimplePlugin("OpsMeshExecutionContext", interceptors=[ExecutionContextInterceptor()]),
        ],
        data_converter=replace(
            DataConverter(),
            failure_converter_class=WorkspaceFailureConverter,
            payload_codec=EncryptedPayloadCodec(
                SecretCipher(
                    key=settings.credential_encryption_key,
                    key_id=settings.credential_encryption_key_id,
                    previous_keys=settings.credential_encryption_previous_keys,
                )
            ),
        ),
    )
