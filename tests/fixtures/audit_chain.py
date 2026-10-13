"""Verify correlation and transactional audit while every exporter is disabled."""

import asyncio
import logging
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from opentelemetry import trace
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from opsmesh.bootstrap.models import register_models
from opsmesh.bootstrap.telemetry import configure_api_telemetry
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.governance.audit.service import AuditService
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.client import TemporalExecutionClient, connect_temporal
from opsmesh.runtime.temporal.contracts import DispatchRequest, ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.config import Settings
from opsmesh.shared.http.middleware import RequestContextMiddleware
from opsmesh.shared.telemetry.logging import (
    RequestContextFilter,
    configure_logging,
    shutdown_logging,
)
from opsmesh.shared.telemetry.request_context import (
    current_evidence_context,
    reset_log_context,
    set_log_context,
)
from tests.fixtures.audit_chain_workflow import AuditChainWorkflow, AuditProbeActivities
from tests.fixtures.database import temporary_postgres_schema
from tests.fixtures.temporal import seed_workspace


class ProbeLogHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []
        self.addFilter(RequestContextFilter())

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() == "Audit probe":
            self.records.append(record)


def verify(flags: str) -> None:
    register_models()
    with temporary_postgres_schema() as engine:
        from opsmesh.shared.db.base import Base

        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        with sessions() as session:
            owners = {}
            for _ in range(2):
                owner, workspace = seed_workspace(session)
                owners[workspace.id] = owner.id
            session.commit()
        settings = Settings(environment="test", tracing_enabled=False, otel_logs_enabled=False)
        configure_logging(settings)
        capture = ProbeLogHandler()
        logging.getLogger().addHandler(capture)
        app = FastAPI()
        app.add_middleware(RequestContextMiddleware, settings=settings)
        runtime = configure_api_telemetry(app, settings, engine=engine)
        assert not runtime.enabled and runtime.provider is not None
        assert runtime.log_handler is None and runtime.logger_provider is None

        @app.post("/submit/{workspace_id}")
        def submit(workspace_id: UUID) -> dict[str, str]:
            tokens = set_log_context(workspace_id=workspace_id, user_id=owners[workspace_id])
            try:
                evidence = current_evidence_context()
                request = ExecutionRequest(
                    workspace_id=workspace_id,
                    resource_id=workspace_id,
                    kind=ExecutionKind.AUDIT_INTEGRITY,
                    requested_by_user_id=owners[workspace_id],
                    request_id=evidence["request_id"],
                    trace_id=evidence["trace_id"],
                )
                with sessions.begin() as session:
                    AuditService(session).record_user_action(
                        workspace_id=workspace_id,
                        user_id=owners[workspace_id],
                        action="acceptance.api",
                        target_type="workspace",
                        target_id=workspace_id,
                    )
                    stage_execution(session, request)
                return {"execution_id": str(request.execution_id), "trace_id": evidence["trace_id"]}
            finally:
                reset_log_context(tokens)

        async def scenario() -> None:
            environment = await WorkflowEnvironment.start_local(
                dev_server_log_level="error"
            )
            try:
                target = environment.client.service_client.config.target_host
                client = await connect_temporal(
                    settings.model_copy(
                        update={
                            "temporal_address": target,
                        }
                    )
                )
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://acceptance"
                ) as api:
                    responses = await asyncio.gather(
                        *[
                            api.post(
                                f"/submit/{workspace_id}",
                                headers={
                                    "traceparent": f"00-{index + 1:032x}-abcdef0123456789-{flags}",
                                    "tracestate": "acceptance=parent",
                                    "X-Request-ID": f"request-{index}",
                                },
                            )
                            for index, workspace_id in enumerate(owners)
                        ]
                    )
                assert all(response.status_code == 200 for response in responses)
                assert current_evidence_context() == {}
                commands = []
                with sessions() as session:
                    for response in responses:
                        row = session.get(
                            TemporalOutboxEntry, UUID(response.json()["execution_id"])
                        )
                        command = DispatchRequest.model_validate(row.payload)
                        assert command.traceparent.endswith(f"-{flags}")
                        assert command.tracestate == "acceptance=parent"
                        commands.append(command)
                activities = AuditProbeActivities(ActivityDependencies(sessions, settings))
                task_queue = f"audit-chain-{uuid4().hex}"
                async with Worker(
                    client,
                    task_queue=task_queue,
                    workflows=[AuditChainWorkflow],
                    activities=[activities.record],
                ):
                    dispatcher = TemporalExecutionClient(
                        client,
                        task_queue=task_queue,
                        workflows={ExecutionKind.AUDIT_INTEGRITY: "acceptance.audit_chain"},
                    )
                    await asyncio.gather(*(dispatcher.dispatch(command) for command in commands))
                    results = await asyncio.gather(
                        *[
                            asyncio.wait_for(
                                client.get_workflow_handle(command.workflow_id).result(), 20
                            )
                            for command in commands
                        ]
                    )
                assert current_evidence_context() == {}
                assert len(capture.records) == 2
                with sessions() as session:
                    for command, result in zip(commands, results, strict=True):
                        assert result["trace_id"] == command.execution.trace_id
                        assert result["workspace_id"] == str(command.workspace_id)
                        assert result["execution_id"] == str(command.execution.execution_id)
                        assert result["request_id"] == command.execution.request_id
                        assert result["workflow_id"] == command.workflow_id
                        event = session.get(AuditEvent, UUID(result["audit_id"]))
                        assert event.trace_id == result["trace_id"]
                        assert event.span_id == result["span_id"]
                        assert event.audit_metadata["execution_id"] == result["execution_id"]
                        assert event.audit_metadata["api_key"] == "[redacted]"
                        record = next(r for r in capture.records if r.audit_id == str(event.id))
                        assert record.trace_id == record.otelTraceID == event.trace_id
                        assert record.span_id == record.otelSpanID == event.span_id
                        audits = list(
                            session.scalars(
                                select(AuditEvent).where(
                                    AuditEvent.workspace_id == command.workspace_id
                                )
                            )
                        )
                        assert len(audits) == 2 and {a.trace_id for a in audits} == {event.trace_id}
                        assert (
                            AuditService(session)
                            .verify_workspace_hash_chain(command.workspace_id)
                            .valid
                        )
                assert not trace.get_current_span().get_span_context().is_valid
            finally:
                await environment.shutdown()

        try:
            asyncio.run(scenario())
        finally:
            logging.getLogger().removeHandler(capture)
            runtime.shutdown()
            shutdown_logging()
