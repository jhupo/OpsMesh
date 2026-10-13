"""The admitted signed distribution intent is delivered and retried by native Temporal."""

import asyncio
import base64
import hashlib
import os
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opsmesh_plugin_sdk.packaging.distribution import (
    CatalogEntry,
    PluginCatalog,
    PluginReleaseDescriptor,
    sign_release,
)
from opsmesh_plugin_sdk.packaging.manifest import PluginManifest
from opsmesh_plugin_sdk.packaging.packages import sign_package
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker
from temporalio.client import WorkflowExecutionStatus, WorkflowFailureError
from temporalio.exceptions import CancelledError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from opsmesh.bootstrap.models import register_models
from opsmesh.capabilities.plugins.contracts import PluginSourceSettings
from opsmesh.capabilities.plugins.distribution import PluginDistributionService
from opsmesh.capabilities.plugins.download_workflow import PluginDownloadWorkflow
from opsmesh.capabilities.plugins.downloads import PluginDownloadActivities
from opsmesh.capabilities.plugins.models import PluginCandidate, PluginDownload, PluginTrustKey
from opsmesh.capabilities.plugins.transport import PluginHTTPFetcher
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.identity.auth.models import UserAPIToken
from opsmesh.identity.authorization.context import AuthenticatedUser
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.client import TemporalExecutionClient
from opsmesh.runtime.temporal.contracts import DispatchRequest
from opsmesh.runtime.temporal.converter import sdk_data_converter
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from opsmesh.runtime.temporal.outbox import TemporalOutboxPublisher
from opsmesh.shared.config import Settings
from tests.fixtures.database import temporary_postgres_schema
from tests.fixtures.temporal import seed_workspace

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL") or os.getenv("OPSMESH_TEST_TEMPORAL") != "1",
    reason="Real PostgreSQL and native Temporal required",
)


def _pin_distribution_host(monkeypatch):
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if host == "plugins.example.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def _signed_distribution():
    private = Ed25519PrivateKey.generate()
    manifest = PluginManifest.model_validate(
        {
            "key": "native.download",
            "version": "1.0.0",
            "name": "Native download",
            "capabilities": [
                {
                    "key": "entry",
                    "kind": "channel",
                    "features": ["receive", "send"],
                    "title": "Entry",
                }
            ],
        }
    )
    release = (
        sign_release(
            PluginReleaseDescriptor(
                package=sign_package(manifest, "publisher", private.private_bytes_raw()),
                platform_requires=">=0.1.0rc1,<1",
                sdk_requires=">=0.2,<1",
                license="Apache-2.0",
                source_repository="https://github.com/example/plugin",
                source_commit="a" * 40,
            ),
            private.private_bytes_raw(),
        )
        .model_dump_json()
        .encode()
    )
    catalog = (
        PluginCatalog(
            entries=[
                CatalogEntry(
                    plugin_key=manifest.key,
                    version=manifest.version,
                    publisher_key_id="publisher",
                    release_url="https://plugins.example.test/release.json",
                    sha256=hashlib.sha256(release).hexdigest(),
                )
            ]
        )
        .model_dump_json()
        .encode()
    )
    return private, catalog, release


def test_native_download_retry_publication_and_explicit_retry_are_idempotent(monkeypatch):
    _pin_distribution_host(monkeypatch)
    private, catalog, release = _signed_distribution()
    calls = []
    corrupt_release = True

    def transfer(request):
        calls.append(request.url.path)
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "plugins.example.test"
        if request.url.path == "/catalog.json" and calls.count("/catalog.json") == 1:
            raise httpx.ConnectError("Transient offline transport failure", request=request)
        content = catalog if request.url.path == "/catalog.json" else release
        if request.url.path == "/release.json" and corrupt_release:
            content = b"corrupted asset"
        return httpx.Response(200, stream=httpx.ByteStream(content))

    with temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        settings = Settings(environment="test", temporal_task_queue=f"download-{uuid4().hex}")
        with sessions() as session:
            owner, workspace = seed_workspace(session)
            actor = AuthenticatedUser.from_model(owner)
            service = PluginDistributionService(session)
            source = service.configure(
                workspace.id,
                owner.id,
                PluginSourceSettings(
                    name="Signed source",
                    url="https://plugins.example.test/catalog.json",
                    sha256=hashlib.sha256(catalog).hexdigest(),
                    allowed_hosts=["plugins.example.test"],
                ),
            )
            source_id, workspace_id = source.id, workspace.id
            job = service.enqueue(workspace_id, actor, source_id, "catalog")
            catalog_job_id, catalog_execution_id = job.id, job.execution_id
            assert service.enqueue(workspace_id, actor, source_id, "catalog").id == job.id
            assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 1
            session.add(
                PluginTrustKey(
                    workspace_id=workspace_id,
                    key_id="publisher",
                    plugin_key="native.download",
                    public_key=base64.b64encode(private.public_key().public_bytes_raw()).decode(),
                )
            )
            session.commit()
        activities = PluginDownloadActivities(
            ActivityDependencies(sessions, settings),
            PluginHTTPFetcher(httpx.MockTransport(transfer)),
        )

        async def scenario():
            nonlocal corrupt_release
            environment = await WorkflowEnvironment.start_local(
                data_converter=sdk_data_converter
            )
            async_engine = create_async_engine(engine.url)
            client = TemporalExecutionClient(
                environment.client, task_queue=settings.temporal_task_queue
            )
            publisher = TemporalOutboxPublisher(async_sessionmaker(async_engine), client)

            async def deliver(identifier, expect_failure=False):
                with sessions() as session:
                    intent = session.get(TemporalOutboxEntry, identifier)
                    request = DispatchRequest.model_validate(intent.payload)
                assert await publisher.publish_pending() == 1
                handle = environment.client.get_workflow_handle(request.workflow_id)
                if expect_failure:
                    with pytest.raises(WorkflowFailureError):
                        await asyncio.wait_for(handle.result(), 30)
                else:
                    await asyncio.wait_for(handle.result(), 30)
                return request

            try:
                with ThreadPoolExecutor(4) as executor:
                    async with Worker(
                        environment.client,
                        task_queue=settings.temporal_task_queue,
                        workflows=[PluginDownloadWorkflow],
                        activities=[activities.download, activities.failed],
                        activity_executor=executor,
                        max_concurrent_activities=4,
                    ):
                        first = await deliver(catalog_execution_id)
                        await client.dispatch(first)
                        with sessions() as session:
                            job = session.get(PluginDownload, catalog_job_id)
                            assert job.status == "succeeded" and job.attempts == 2
                            candidate = session.scalar(select(PluginCandidate))
                            job = PluginDistributionService(session).enqueue(
                                workspace_id, actor, source_id, "release", candidate.id
                            )
                            release_job_id, release_execution_id = job.id, job.execution_id
                        old = await deliver(release_execution_id, expect_failure=True)
                        with sessions() as session:
                            failed = session.get(PluginDownload, release_job_id)
                            assert failed.status == "failed"
                            assert failed.error_code == "checksum_mismatch" and failed.attempts == 1
                            assert session.scalar(select(PluginCandidate)).verified_release is None
                            retry = PluginDistributionService(session).retry(
                                workspace_id, actor, release_job_id
                            )
                            assert retry.execution_id != release_execution_id
                            retry_execution_id = retry.execution_id
                        corrupt_release = False
                        await deliver(retry_execution_id)
                        # A delayed completion of the preceding attempt cannot overwrite
                        # the new execution, even though it belongs to the same download.
                        activities.failed(old.execution, "download_failed")
                        return release_job_id
            finally:
                await async_engine.dispose()
                await environment.shutdown()

        release_job_id = asyncio.run(scenario())
        with sessions() as session:
            assert session.get(PluginDownload, release_job_id).status == "succeeded"
            assert session.scalar(select(PluginCandidate)).verified_release is not None
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.action == "plugin.download_succeeded")
                )
                == 2
            )
            assert all(session.scalars(select(TemporalOutboxEntry.published_at)))
        assert calls.count("/catalog.json") == 2
        assert calls.count("/release.json") == 2


def test_download_rechecks_source_before_atomic_publication(monkeypatch):
    _pin_distribution_host(monkeypatch)
    _, catalog, _ = _signed_distribution()
    with temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        settings = Settings(
            environment="test", temporal_task_queue=f"revoked-download-{uuid4().hex}"
        )
        with sessions() as session:
            owner, workspace = seed_workspace(session)
            source = PluginDistributionService(session).configure(
                workspace.id,
                owner.id,
                PluginSourceSettings(
                    name="Revocable source",
                    url="https://plugins.example.test/catalog.json",
                    sha256=hashlib.sha256(catalog).hexdigest(),
                    allowed_hosts=["plugins.example.test"],
                ),
            )
            job = PluginDistributionService(session).enqueue(
                workspace.id, AuthenticatedUser.from_model(owner), source.id, "revoke"
            )
            reference = DispatchRequest.model_validate(
                session.get(TemporalOutboxEntry, job.execution_id).payload
            )
            job_id, source_id, workspace_id = job.id, source.id, workspace.id
        calls = []

        def transfer(request):
            # This independent transaction can acquire the workspace lock while
            # transfer is running; external I/O must not retain a database lease.
            with sessions() as session:
                source = PluginDistributionService(session).source(workspace_id, source_id)
                source.generation += 1
                session.commit()
            calls.append(request.url.path)
            return httpx.Response(200, stream=httpx.ByteStream(catalog))

        activities = PluginDownloadActivities(
            ActivityDependencies(sessions, settings),
            PluginHTTPFetcher(httpx.MockTransport(transfer)),
        )

        async def scenario():
            environment = await WorkflowEnvironment.start_local(
                data_converter=sdk_data_converter
            )
            try:
                with ThreadPoolExecutor(4) as executor:
                    async with Worker(
                        environment.client,
                        task_queue=settings.temporal_task_queue,
                        workflows=[PluginDownloadWorkflow],
                        activities=[activities.download, activities.failed],
                        activity_executor=executor,
                        max_concurrent_activities=4,
                    ):
                        await TemporalExecutionClient(
                            environment.client, task_queue=settings.temporal_task_queue
                        ).dispatch(reference)
                        with pytest.raises(WorkflowFailureError):
                            await asyncio.wait_for(
                                environment.client.get_workflow_handle(
                                    reference.workflow_id
                                ).result(),
                                30,
                            )
            finally:
                await environment.shutdown()

        asyncio.run(scenario())
        with sessions() as session:
            assert session.get(PluginDownload, job_id).error_code == "verification_rejected"
            assert session.scalar(select(func.count()).select_from(PluginCandidate)) == 0
        assert calls == ["/catalog.json"]


@pytest.mark.parametrize("revocation", ["token", "scope", "workspace"])
def test_download_rechecks_the_accepted_principal_before_network_io(monkeypatch, revocation):
    _pin_distribution_host(monkeypatch)
    _, catalog, _ = _signed_distribution()
    with temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        settings = Settings(environment="test", temporal_task_queue=f"principal-{uuid4().hex}")
        with sessions() as session:
            owner, workspace = seed_workspace(session)
            token = UserAPIToken(
                user_id=owner.id,
                name="Accepted token",
                token_hash=uuid4().hex,
                fingerprint=uuid4().hex,
                scopes=None,
            )
            session.add(token)
            session.commit()
            source = PluginDistributionService(session).configure(
                workspace.id,
                owner.id,
                PluginSourceSettings(
                    name="Principal source",
                    url="https://plugins.example.test/catalog.json",
                    sha256=hashlib.sha256(catalog).hexdigest(),
                    allowed_hosts=["plugins.example.test"],
                ),
            )
            job = PluginDistributionService(session).enqueue(
                workspace.id,
                AuthenticatedUser.from_model(owner, token_id=token.id),
                source.id,
                "principal",
            )
            request = DispatchRequest.model_validate(
                session.get(TemporalOutboxEntry, job.execution_id).payload
            )
            job_id = job.id
            if revocation == "token":
                token.status = "revoked"
            elif revocation == "scope":
                token.scopes = {
                    "workspace_ids": [str(workspace.id)],
                    "workspace_actions": ["read"],
                    "account_actions": [],
                }
            else:
                workspace.status = "frozen"
            session.commit()
        calls = []

        def transfer(request):
            calls.append(request.url.path)
            return httpx.Response(200, stream=httpx.ByteStream(catalog))

        activities = PluginDownloadActivities(
            ActivityDependencies(sessions, settings),
            PluginHTTPFetcher(httpx.MockTransport(transfer)),
        )

        async def scenario():
            environment = await WorkflowEnvironment.start_local(
                data_converter=sdk_data_converter
            )
            try:
                with ThreadPoolExecutor(4) as executor:
                    async with Worker(
                        environment.client,
                        task_queue=settings.temporal_task_queue,
                        workflows=[PluginDownloadWorkflow],
                        activities=[activities.download, activities.failed],
                        activity_executor=executor,
                        max_concurrent_activities=4,
                    ):
                        await TemporalExecutionClient(
                            environment.client, task_queue=settings.temporal_task_queue
                        ).dispatch(request)
                        with pytest.raises(WorkflowFailureError):
                            await asyncio.wait_for(
                                environment.client.get_workflow_handle(
                                    request.workflow_id
                                ).result(),
                                30,
                            )
            finally:
                await environment.shutdown()

        asyncio.run(scenario())
        with sessions() as session:
            row = session.get(PluginDownload, job_id)
            assert row.status == "failed"
            assert row.error_code in {"verification_rejected", "WorkspaceUnavailable"}
        assert calls == []


def test_plugin_download_migration_preserves_terminal_facts_and_requires_explicit_retry():
    with temporary_postgres_schema() as engine:
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url", engine.url.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, "0139_restore_drill_receipts")
        with Session(engine) as session:
            owner, workspace = seed_workspace(session)
            source = PluginDistributionService(session).configure(
                workspace.id,
                owner.id,
                PluginSourceSettings(
                    name="Preserved source",
                    url="https://plugins.example.test/catalog.json",
                    sha256="a" * 64,
                    allowed_hosts=["plugins.example.test"],
                ),
            )
            source_id, owner_id, workspace_id = source.id, owner.id, workspace.id
        identifiers = {}
        with engine.begin() as connection:
            for status in ("queued", "fetching", "succeeded", "failed"):
                identifier = identifiers[status] = uuid4()
                connection.execute(
                    text(
                        "INSERT INTO plugin_downloads (id, workspace_id, source_id, actor_id, "
                        "request_key, source_generation, status, attempts, error_code) "
                        "VALUES (:id, :workspace, :source, :actor, :status, 1, :status, 1, :error)"
                    ),
                    {
                        "id": identifier,
                        "workspace": workspace_id,
                        "source": source_id,
                        "actor": owner_id,
                        "status": status,
                        "error": "original" if status == "failed" else None,
                    },
                )
        command.upgrade(config, "0140_native_plugin_downloads")
        with Session(engine) as session:
            for previous, identifier in identifiers.items():
                row = session.get(PluginDownload, identifier)
                assert row.execution_id is not None
                if previous in {"queued", "fetching"}:
                    assert (
                        row.status == "failed"
                        and row.error_code == "retired_execution_requires_retry"
                    )
                else:
                    assert row.status == previous
            assert session.scalar(select(func.count()).select_from(TemporalOutboxEntry)) == 0
        command.downgrade(config, "0139_restore_drill_receipts")
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT status FROM plugin_downloads WHERE id = :id"),
                    {"id": identifiers["succeeded"]},
                )
                == "succeeded"
            )
            connection.execute(text("SELECT lease_token, lease_until FROM plugin_downloads"))


def test_native_download_cancellation_stops_transfer_without_publishing(monkeypatch):
    _pin_distribution_host(monkeypatch)
    _, catalog, _ = _signed_distribution()
    started = asyncio.Event()
    calls = []

    async def transfer(request):
        calls.append(request.url.path)
        started.set()
        await asyncio.sleep(60)
        return httpx.Response(200, stream=httpx.ByteStream(catalog))

    with temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        settings = Settings(
            environment="test", temporal_task_queue=f"cancel-download-{uuid4().hex}"
        )
        with sessions() as session:
            owner, workspace = seed_workspace(session)
            service = PluginDistributionService(session)
            source = service.configure(
                workspace.id,
                owner.id,
                PluginSourceSettings(
                    name="Cancelled source",
                    url="https://plugins.example.test/catalog.json",
                    sha256=hashlib.sha256(catalog).hexdigest(),
                    allowed_hosts=["plugins.example.test"],
                ),
            )
            job = service.enqueue(
                workspace.id, AuthenticatedUser.from_model(owner), source.id, "cancel"
            )
            request = DispatchRequest.model_validate(
                session.get(TemporalOutboxEntry, job.execution_id).payload
            )
            job_id = job.id
        activities = PluginDownloadActivities(
            ActivityDependencies(sessions, settings),
            PluginHTTPFetcher(httpx.MockTransport(transfer)),
        )

        async def scenario():
            environment = await WorkflowEnvironment.start_local(
                data_converter=sdk_data_converter
            )
            try:
                with ThreadPoolExecutor(4) as executor:
                    async with Worker(
                        environment.client,
                        task_queue=settings.temporal_task_queue,
                        workflows=[PluginDownloadWorkflow],
                        activities=[activities.download, activities.failed],
                        activity_executor=executor,
                        max_concurrent_activities=4,
                        max_heartbeat_throttle_interval=timedelta(seconds=1),
                    ):
                        await TemporalExecutionClient(
                            environment.client, task_queue=settings.temporal_task_queue
                        ).dispatch(request)
                        await asyncio.wait_for(started.wait(), 15)
                        handle = environment.client.get_workflow_handle(request.workflow_id)
                        await handle.cancel()
                        with pytest.raises(WorkflowFailureError) as failure:
                            await asyncio.wait_for(handle.result(), 15)
                        assert isinstance(failure.value.cause, CancelledError)
                        assert (await handle.describe()).status == WorkflowExecutionStatus.CANCELED
            finally:
                await environment.shutdown()

        asyncio.run(scenario())
        with sessions() as session:
            row = session.get(PluginDownload, job_id)
            assert row.status == "failed" and row.error_code == "download_cancelled"
            assert session.scalar(select(func.count()).select_from(PluginCandidate)) == 0
        assert calls == ["/catalog.json"]
