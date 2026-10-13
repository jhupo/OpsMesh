"""Native periodic platform maintenance and scoped administrator execution."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from opsmesh.bootstrap.models import register_models
from opsmesh.capabilities.mcp.models import MCPToolCallLog
from opsmesh.governance.audit.integrity import AuditIntegrityService
from opsmesh.governance.audit.models import AuditIntegrityCheck
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.platform.maintenance.activities import PlatformMaintenanceActivities
from opsmesh.platform.maintenance.contracts import (
    MaintenanceOperation,
    PlatformMaintenanceRequest,
    WorkspaceMaintenanceRequest,
)
from opsmesh.platform.maintenance.schedules import ensure_platform_schedules
from opsmesh.platform.maintenance.workflow import (
    PlatformMaintenanceWorkflow,
    WorkspaceMaintenanceWorkflow,
)
from opsmesh.platform.operations.activities import AdminOperationActivities
from opsmesh.platform.operations.models import AdminOperationRecord, PlatformMetricSnapshot
from opsmesh.platform.operations.service import AdminOperationService
from opsmesh.platform.operations.workflow import AdminOperationWorkflow
from opsmesh.resources.memory.configuration import WorkspaceMemoryConfigurationService
from opsmesh.resources.memory.models import (
    WorkspaceMemoryEntry,
    WorkspaceMemoryLifecycleEvent,
    memory_content_fingerprint,
)
from opsmesh.resources.transfers.models import WorkspaceExportJob
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.client import TemporalExecutionClient
from opsmesh.runtime.temporal.contracts import DispatchRequest
from opsmesh.runtime.temporal.converter import sdk_data_converter
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.shared.config import Settings
from tests.fixtures.admin_api import _seed_workspace
from tests.fixtures.database import temporary_postgres_schema

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL") or os.getenv("OPSMESH_TEST_TEMPORAL") != "1",
    reason="Real PostgreSQL and native Temporal required",
)


def test_native_schedules_execute_history_audit_diagnostics_and_backup(tmp_path):
    with temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        settings = Settings(
            environment="test",
            temporal_task_queue="platform-maintenance",
            storage_root=str(tmp_path),
        )
        with sessions() as session:
            user, workspace = _seed_workspace(session)
            user.platform_admin = True
            workspace.settings = {
                "data_lifecycle": {"backup": {"enabled": True, "schedule": "hourly"}}
            }
            frozen = _seed_workspace(session, email="frozen@example.com", slug="frozen")[1]
            episodes = []
            for scoped in (workspace, frozen):
                WorkspaceMemoryConfigurationService(session).create_default(scoped.id)
                episode = WorkspaceMemoryEntry(
                    workspace_id=scoped.id,
                    source_type="agent_run",
                    source_id=uuid4().hex,
                    memory_layer="episodic",
                    scope_type="workspace",
                    scope_id=str(scoped.id),
                    memory_key="expired",
                    entry_type="agent_run_completed",
                    title="Expired",
                    content="Expired episode",
                    content_fingerprint=memory_content_fingerprint("Expired", "Expired episode"),
                    expires_at=datetime.now(UTC) - timedelta(days=1),
                )
                session.add(episode)
                episodes.append(episode)
            frozen.status = "frozen"
            session.add(
                MCPToolCallLog(
                    workspace_id=workspace.id,
                    tool_name="offline",
                    status="failed",
                    error={"code": "test", "diagnostic": {"summary": "Expired detail"}},
                    created_at=datetime.now(UTC) - timedelta(days=30),
                )
            )
            session.commit()
            episode_ids = [episode.id for episode in episodes]
            row = AdminOperationService(session).submit(
                request_id=uuid4(),
                workspace_id=workspace.id,
                actor=AuthenticatedUser.from_model(user),
                operation="verify_audit",
                parameters={"reason": "Native verification"},
            )
            session.commit()
            request_id = row.id
        maintenance = PlatformMaintenanceActivities(ActivityDependencies(sessions, settings))
        admin = AdminOperationActivities(ActivityDependencies(sessions, settings))

        async def scenario():
            env = await WorkflowEnvironment.start_local(
                data_converter=sdk_data_converter
            )
            try:
                async with Worker(
                    env.client,
                    task_queue=settings.temporal_task_queue,
                    workflows=[
                        PlatformMaintenanceWorkflow,
                        WorkspaceMaintenanceWorkflow,
                        AdminOperationWorkflow,
                    ],
                    activities=[
                        maintenance.global_operation,
                        maintenance.workspaces,
                        maintenance.workspace_operation,
                        admin.execute,
                    ],
                    activity_executor=ThreadPoolExecutor(4),
                ):
                    await ensure_platform_schedules(env.client, settings)
                    await ensure_platform_schedules(env.client, settings)
                    for operation in MaintenanceOperation:
                        handle = env.client.get_schedule_handle(
                            f"opsmesh:platform:maintenance:{operation.value}"
                        )
                        await handle.trigger()
                        for _ in range(300):
                            description = await handle.describe()
                            if description.info.recent_actions:
                                execution = description.info.recent_actions[-1].action
                                await asyncio.wait_for(
                                    env.client.get_workflow_handle(execution.workflow_id).result(),
                                    30,
                                )
                                break
                            await asyncio.sleep(0.05)
                        else:
                            raise AssertionError("Native schedule did not start its Workflow")
                    with sessions() as session:
                        dispatch = session.scalar(
                            select(TemporalOutboxEntry).where(
                                TemporalOutboxEntry.workflow_id.contains(str(request_id))
                            )
                        )
                        await TemporalExecutionClient(
                            env.client, task_queue=settings.temporal_task_queue
                        ).dispatch(DispatchRequest.model_validate(dispatch.payload))
                        await asyncio.wait_for(
                            env.client.get_workflow_handle(dispatch.workflow_id).result(), 30
                        )
            finally:
                await env.shutdown()

        asyncio.run(scenario())
        with sessions() as session:
            assert session.scalar(select(func.count()).select_from(PlatformMetricSnapshot)) >= 1
            assert session.scalar(select(func.count()).select_from(AuditIntegrityCheck)) >= 2
            assert session.scalar(select(func.count()).select_from(WorkspaceExportJob)) == 1
            log = session.scalar(select(MCPToolCallLog))
            assert "diagnostic" not in log.error
            assert log.error["diagnostic_expired"] is True
            assert session.get(AdminOperationRecord, request_id).status == "completed"
            assert session.get(WorkspaceMemoryEntry, episode_ids[0]).status == "archived"
            assert session.get(WorkspaceMemoryEntry, episode_ids[1]).status == "active"
            assert (
                session.scalar(select(func.count()).select_from(WorkspaceMemoryLifecycleEvent)) == 1
            )


def test_workspace_maintenance_failure_does_not_cancel_other_tenants(monkeypatch):
    with temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        settings = Settings(environment="test", temporal_task_queue="maintenance-isolation")
        with sessions() as session:
            identifiers = [
                _seed_workspace(session, email=f"audit-{index}@example.com", slug=f"audit-{index}")[
                    1
                ].id
                for index in range(3)
            ]
        original = AuditIntegrityService.verify_and_record_workspace_integrity
        failed_calls = []

        def verify_and_record_workspace_integrity(self, workspace_id, **kwargs):
            if workspace_id == identifiers[0]:
                failed_calls.append(workspace_id)
                raise ValueError("Deliberate isolated maintenance failure")
            return original(self, workspace_id, **kwargs)

        monkeypatch.setattr(
            AuditIntegrityService,
            "verify_and_record_workspace_integrity",
            verify_and_record_workspace_integrity,
        )
        maintenance = PlatformMaintenanceActivities(ActivityDependencies(sessions, settings))

        async def scenario():
            env = await WorkflowEnvironment.start_local(
                data_converter=sdk_data_converter
            )
            try:
                async with Worker(
                    env.client,
                    task_queue=settings.temporal_task_queue,
                    workflows=[PlatformMaintenanceWorkflow, WorkspaceMaintenanceWorkflow],
                    activities=[maintenance.workspaces, maintenance.workspace_operation],
                    activity_executor=ThreadPoolExecutor(4),
                ):
                    with pytest.raises(WorkflowFailureError) as failure:
                        await asyncio.wait_for(
                            env.client.execute_workflow(
                                PlatformMaintenanceWorkflow.run,
                                PlatformMaintenanceRequest(operation=MaintenanceOperation.AUDIT),
                                id="tenant-maintenance-isolation",
                                task_queue=settings.temporal_task_queue,
                            ),
                            30,
                        )
                    assert failure.value.cause.type == "workspace_maintenance_failed"
                    assert "1 workspace" in str(failure.value.cause)
            finally:
                await env.shutdown()

        asyncio.run(scenario())
        assert failed_calls == [identifiers[0]]
        with sessions() as session:
            checked = set(session.scalars(select(AuditIntegrityCheck.workspace_id)))
            assert checked == set(identifiers[1:])


def test_audit_maintenance_does_not_lock_workspace_during_hash_scan(monkeypatch):
    with temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        settings = Settings(environment="test", temporal_task_queue="maintenance-lock-boundary")
        with sessions() as session:
            user, workspace = _seed_workspace(session)
            user_id, workspace_id = user.id, workspace.id
        scanning, release = Event(), Event()
        original = AuditService.verify_workspace_hash_chain

        def verify(self, identifier):
            scanning.set()
            assert release.wait(10), "Audit verification was not released"
            return original(self, identifier)

        monkeypatch.setattr(AuditService, "verify_workspace_hash_chain", verify)
        maintenance = PlatformMaintenanceActivities(ActivityDependencies(sessions, settings))

        def write_audit():
            with sessions.begin() as session:
                session.execute(text("SET LOCAL lock_timeout = '1s'"))
                AuditService(session).record_user_action(
                    workspace_id=workspace_id,
                    user_id=user_id,
                    action="workspace.concurrent_request",
                    target_type="workspace",
                    target_id=workspace_id,
                )

        async def scenario():
            env = await WorkflowEnvironment.start_local(
                data_converter=sdk_data_converter
            )
            try:
                with ThreadPoolExecutor(2) as executor:
                    async with Worker(
                        env.client,
                        task_queue=settings.temporal_task_queue,
                        workflows=[WorkspaceMaintenanceWorkflow],
                        activities=[maintenance.workspace_operation],
                        activity_executor=executor,
                    ):
                        handle = await env.client.start_workflow(
                            WorkspaceMaintenanceWorkflow.run,
                            WorkspaceMaintenanceRequest(
                                operation=MaintenanceOperation.AUDIT,
                                workspace_id=workspace_id,
                            ),
                            id="audit-hash-scan-lock-boundary",
                            task_queue=settings.temporal_task_queue,
                        )
                        try:
                            assert await asyncio.to_thread(scanning.wait, 10)
                            await asyncio.wait_for(asyncio.to_thread(write_audit), 3)
                        finally:
                            release.set()
                        await asyncio.wait_for(handle.result(), 10)
            finally:
                release.set()
                await env.shutdown()

        asyncio.run(scenario())
        with sessions() as session:
            check = session.scalar(select(AuditIntegrityCheck))
            assert check.valid is True
            assert check.checked_events >= 1
