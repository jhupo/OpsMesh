import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from opsmesh.runtime.temporal.contracts import ExecutionRequest

_SAFE_RETRY = RetryPolicy(maximum_attempts=3)
_EXTERNAL_OPERATION_RETRY = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="opsmesh.memory_index")
class MemoryIndexWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        await workflow.execute_activity(
            "opsmesh.memory.index",
            request,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_SAFE_RETRY,
        )


@workflow.defn(name="opsmesh.memory_embed")
class MemoryEmbeddingWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        await workflow.execute_activity(
            "opsmesh.memory.embed",
            request,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_SAFE_RETRY,
        )


@workflow.defn(name="opsmesh.knowledge_ingest")
class KnowledgeIngestionWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        await workflow.execute_activity(
            "opsmesh.knowledge.ingest",
            request,
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=_SAFE_RETRY,
        )


@workflow.defn(name="opsmesh.archive_export")
class ArchiveExportWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        try:
            queue = await workflow.execute_activity(
                "opsmesh.archive.prepare",
                request,
                result_type=str,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=_SAFE_RETRY,
            )
            if not queue:
                return
            await workflow.execute_activity(
                "opsmesh.archive.export",
                request,
                task_queue=queue,
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=timedelta(seconds=30),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                retry_policy=_EXTERNAL_OPERATION_RETRY,
            )
        except (ActivityError, asyncio.CancelledError):
            await asyncio.shield(
                workflow.execute_activity(
                    "opsmesh.archive.interrupted",
                    request,
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=_SAFE_RETRY,
                )
            )
            raise


@workflow.defn(name="opsmesh.archive_verify")
class ArchiveVerificationWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        try:
            queue = await workflow.execute_activity(
                "opsmesh.archive.prepare",
                request,
                result_type=str,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=_SAFE_RETRY,
            )
            if not queue:
                return
            await workflow.execute_activity(
                "opsmesh.archive.verify",
                request,
                task_queue=queue,
                start_to_close_timeout=timedelta(minutes=30),
                heartbeat_timeout=timedelta(seconds=30),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                retry_policy=_SAFE_RETRY,
            )
        except (ActivityError, asyncio.CancelledError):
            await asyncio.shield(
                workflow.execute_activity(
                    "opsmesh.archive.verify_failed",
                    request,
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=_SAFE_RETRY,
                )
            )
            raise


@workflow.defn(name="opsmesh.archive_restore_drill")
class ArchiveRestoreDrillWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        try:
            queue = await workflow.execute_activity(
                "opsmesh.archive.prepare",
                request,
                result_type=str,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=_SAFE_RETRY,
            )
            if not queue:
                return
            await workflow.execute_activity(
                "opsmesh.archive.restore_drill",
                request,
                task_queue=queue,
                start_to_close_timeout=timedelta(hours=2),
                heartbeat_timeout=timedelta(seconds=30),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                retry_policy=_EXTERNAL_OPERATION_RETRY,
            )
        except (ActivityError, asyncio.CancelledError):
            await asyncio.shield(
                workflow.execute_activity(
                    "opsmesh.archive.restore_drill_interrupted",
                    request,
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=_SAFE_RETRY,
                )
            )
            raise


@workflow.defn(name="opsmesh.webhook_delivery")
class WebhookDeliveryWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        while True:
            delay = await workflow.execute_activity(
                "opsmesh.webhook.deliver",
                request,
                result_type=float,
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=_EXTERNAL_OPERATION_RETRY,
            )
            if delay < 0:
                return
            await workflow.sleep(timedelta(seconds=delay))


@workflow.defn(name="opsmesh.secret_reencrypt")
class CredentialReencryptionWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        await workflow.execute_activity(
            "opsmesh.credentials.reencrypt",
            request,
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=_SAFE_RETRY,
        )


@workflow.defn(name="opsmesh.model_provider_health")
class ModelProviderHealthWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        await workflow.execute_activity(
            "opsmesh.model_provider.check_health",
            request,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=_EXTERNAL_OPERATION_RETRY,
        )


@workflow.defn(name="opsmesh.audit_integrity")
class AuditIntegrityWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        await workflow.execute_activity(
            "opsmesh.audit.check_integrity",
            request,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=_EXTERNAL_OPERATION_RETRY,
        )


@workflow.defn(name="opsmesh.mcp_control")
class MCPControlWorkflow:
    @workflow.run
    async def run(self, request: ExecutionRequest) -> None:
        await workflow.execute_activity(
            "opsmesh.mcp.control",
            request,
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=_EXTERNAL_OPERATION_RETRY,
        )


BACKGROUND_WORKFLOWS = (
    MemoryIndexWorkflow,
    MemoryEmbeddingWorkflow,
    KnowledgeIngestionWorkflow,
    ArchiveExportWorkflow,
    ArchiveVerificationWorkflow,
    ArchiveRestoreDrillWorkflow,
    WebhookDeliveryWorkflow,
    CredentialReencryptionWorkflow,
    ModelProviderHealthWorkflow,
    AuditIntegrityWorkflow,
    MCPControlWorkflow,
)
