"""Real ledger transactions around deterministic sandbox responses, without Docker."""

import asyncio
import base64
from uuid import uuid4

import pytest
from sqlalchemy import select

from opsmesh.bootstrap.orchestration import ExecutionAssets
from opsmesh.bootstrap.routers import ConfigurationScopeAdmission
from opsmesh.identity.quotas import service as quotas
from opsmesh.identity.quotas.models import UserQuotaLimit, UserStorageAccount
from opsmesh.orchestration.contracts import ExecutionSubject
from opsmesh.orchestration.runtime.activities import RuntimeActivities
from opsmesh.orchestration.runtime.contracts import RuntimeCommand
from opsmesh.orchestration.runtime.control import RuntimeControlRequest, cancel
from opsmesh.orchestration.runtime.models import (
    Environment,
    EnvironmentFile,
    EnvironmentOperationReceipt,
)
from opsmesh.orchestration.runtime.schemas import EnvironmentOperationRequest
from opsmesh.orchestration.runtime.service import submit_operation
from opsmesh.resources.artifacts.service import read_artifact_content
from opsmesh.resources.files.access import ResourceReader
from opsmesh.shared.errors import DomainError
from tests.execution.test_runs_postgres import harness as harness


class UnusedBackend:
    async def resume(self, state):
        raise AssertionError("No I/O should occur")


def runtime(harness, backend=None):  # noqa: F811
    r = harness.runs
    return RuntimeActivities(
        r.sessions, r.admission, r.bindings, r.cipher, backend or UnusedBackend(), quotas
    )


def environment(harness):  # noqa: F811
    r = harness.runs
    with r.sessions() as session, session.begin():
        secret = r.cipher.encrypt_payload({"native_id": "test-only"})
        record = Environment(
            scope_id=harness.principal.user_id,
            principal_id=harness.principal.user_id,
            owner_ref=f"user:{harness.principal.user_id}",
            status="ready",
            configuration={"image": "test"},
            encrypted_state=secret.ciphertext,
            encryption_key_id=secret.key_id,
        )
        session.add(record)
        session.flush()
        return record.id


def stage(harness, environment_id, operation_id, content=None, action="write", command=None):  # noqa: F811
    request = EnvironmentOperationRequest(
        action=action,
        path="file.txt" if content is not None else None,
        content_base64=base64.b64encode(content).decode() if content is not None else None,
        command=command or [],
    )
    with harness.runs.sessions() as session, session.begin():
        submit_operation(
            session,
            subject=ExecutionSubject(harness.principal.user_id),
            admission=harness.runs.admission,
            environment_id=environment_id,
            operation_id=operation_id,
            request=request,
        )
    return RuntimeCommand(
        execution_id=uuid4(),
        workflow_id="test-runtime",
        environment_id=environment_id,
        principal_id=harness.principal.user_id,
        operation_id=operation_id,
        action=action,
        arguments=request.model_dump(exclude={"action"}, exclude_none=True),
    )


def account(harness):  # noqa: F811
    with harness.runs.sessions() as session:
        row = session.get(UserStorageAccount, harness.principal.user_id)
        return row.used_bytes, row.reserved_bytes


def test_file_overwrite_holds_peak_bytes_and_close_reclaims(harness):  # noqa: F811
    env = environment(harness)
    activities = runtime(harness)
    first = stage(harness, env, "first", b"abc")
    assert activities.claim(first)[0]
    assert account(harness) == (0, 3)
    activities.complete(first, {"bytes_written": 3})
    assert account(harness) == (3, 0)
    second = stage(harness, env, "second", b"xy")
    activities.claim(second)
    assert account(harness) == (3, 2)
    activities.complete(second, {"bytes_written": 2})
    activities.complete(second, {"bytes_written": 2})
    assert account(harness) == (2, 0)
    close = stage(harness, env, "close", action="close")
    activities.claim(close)
    activities.complete(close, {"closed": True})
    assert account(harness) == (0, 0)
    with harness.runs.sessions() as session:
        assert session.scalar(select(EnvironmentFile)) is None


def test_uncertain_write_retains_storage_and_blocks_followup(harness):  # noqa: F811
    env = environment(harness)
    activities = runtime(harness)
    command = stage(harness, env, "unknown-write", b"abc")
    activities.claim(command)
    activities.complete(command, None)
    assert account(harness) == (0, 3)
    with pytest.raises(DomainError):
        activities.claim(command)
    with pytest.raises(DomainError):
        stage(harness, env, "another", b"x")


def test_quota_denial_finishes_pending_receipt_without_io(harness):  # noqa: F811
    env = environment(harness)
    with harness.runs.sessions() as session, session.begin():
        session.get(UserQuotaLimit, harness.principal.user_id).storage_limit_bytes = 1
    command = stage(harness, env, "denied", b"abc")
    with pytest.raises(DomainError):
        asyncio.run(runtime(harness).execute(command))
    assert account(harness) == (0, 0)
    with harness.runs.sessions() as session:
        receipt = session.get(EnvironmentOperationReceipt, (harness.principal.user_id, "denied"))
        record = session.get(Environment, env)
        assert receipt.status == "completed"
        assert record.active_operation_id is None and record.status == "ready"
        assert harness.runs.cipher.decrypt_payload(receipt.encrypted_result)["not_executed"] is True


def test_cancel_pending_prevents_late_activity_claim(harness):  # noqa: F811
    env = environment(harness)
    command = stage(harness, env, "cancel", b"abc")
    with harness.runs.sessions() as session, session.begin():
        receipt = session.get(EnvironmentOperationReceipt, (harness.principal.user_id, "cancel"))
        cancel(
            session,
            receipt=receipt,
            actor_id=harness.principal.user_id,
            request=RuntimeControlRequest(command_id=uuid4(), expected_revision=0),
        )
    claimed, result = runtime(harness).claim(command)
    assert not claimed and result["cancelled"]
    assert account(harness) == (0, 0)


def test_output_is_idempotent_and_storage_is_charged_once(harness, tmp_path):  # noqa: F811
    run = harness.submit()
    harness.claim(run)
    producer = ExecutionAssets(tmp_path, ConfigurationScopeAdmission())
    identifiers = []
    for _ in range(2):
        with harness.runs.sessions() as session, session.begin():
            identifiers.append(
                producer.publish_output(
                    session,
                    subject=ExecutionSubject(harness.principal.user_id),
                    run_id=run.id,
                    name="answer.txt",
                    payload=b"answer",
                    media_type="text/plain",
                )
            )
    assert identifiers[0] == identifiers[1]
    assert account(harness) == (6, 0)
    with harness.runs.sessions() as session:
        _, payload = read_artifact_content(
            session,
            artifact_id=identifiers[0],
            reader=ResourceReader(harness.principal.user_id),
            storage_root=tmp_path,
        )
        assert payload == b"answer"
