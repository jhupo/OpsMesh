"""Compose the native Client, payload codec and Activity execution context."""

from dataclasses import replace
from typing import Any, Protocol, runtime_checkable

from temporalio import activity
from temporalio.client import Client
from temporalio.contrib.opentelemetry import OpenTelemetryPlugin
from temporalio.converter import DataConverter, DefaultFailureConverterWithEncodedAttributes
from temporalio.exceptions import ApplicationError
from temporalio.plugin import SimplePlugin
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor

from opsmesh.config import Settings
from opsmesh.shared.errors import DomainError
from opsmesh.shared.security.secrets import SecretCipher
from opsmesh.telemetry.context import bind_log_context
from opsmesh.temporal.codec import EncryptedPayloadCodec


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
            try:
                return await super().execute_activity(input)
            except DomainError as exc:
                raise ApplicationError(exc.message, type=exc.code, non_retryable=True) from exc


async def connect_temporal(settings: Settings) -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        tls=settings.temporal_tls_enabled,
        api_key=settings.temporal_api_key,
        plugins=[
            OpenTelemetryPlugin(add_temporal_spans=True),
            SimplePlugin("OpsMeshExecutionContext", interceptors=[ExecutionContextInterceptor()]),
        ],
        data_converter=replace(
            DataConverter(),
            failure_converter_class=DefaultFailureConverterWithEncodedAttributes,
            payload_codec=EncryptedPayloadCodec(
                SecretCipher(
                    key=settings.credential_encryption_key,
                    key_id=settings.credential_encryption_key_id,
                    previous_keys=settings.credential_encryption_previous_keys,
                )
            ),
        ),
    )
