"""S5 resource tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must run against a real PostgreSQL instance (never SQLite). They cover the
acceptance surface of V4 section 16.2 for resources: user-scope upload,
versions, write-back compare-and-set, grants, deletion, the storage
reservation and its compensation, and artifact recording under the
rejecting default authorization.
"""

from __future__ import annotations

import threading
import time
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.bootstrap.approvals import build_approvals
from opsmesh.bootstrap.capabilities import IdentityStorageQuotaLedger
from opsmesh.bootstrap.platform import transaction_dependencies
from opsmesh.identity.errors import QuotaExceededError
from opsmesh.identity.quotas import service as quotas
from opsmesh.identity.users import service as users
from opsmesh.identity.users.models import UserProfile
from opsmesh.resources.artifacts import service as artifact_service
from opsmesh.resources.artifacts.service import RejectingArtifactAuthorization
from opsmesh.resources.files import service as file_service
from opsmesh.resources.files.access import RejectingScopeAdmission, ResourceReader, ScopeRef
from opsmesh.resources.files.models import ResourceFile
from opsmesh.resources.storage.filesystem import content_storage_key, new_content_id
from opsmesh.resources.storage.transactions import stage_content, stage_content_removal
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from tests.conftest import FEATURE_TABLES


def open_account(sessions, *, email: str, display_name: str) -> UserProfile:
    with sessions() as session:
        profile = users.create_user(
            session, email=email, display_name=display_name, actor=AuditActor(actor_type="platform")
        )
        quotas.ensure_ledger(session, user_id=profile.id)
        session.commit()
    return profile


def actor_of(profile: UserProfile) -> AuditActor:
    return AuditActor(actor_type="user", actor_id=str(profile.id))


def reader_of(profile: UserProfile) -> ResourceReader:
    return ResourceReader(user_id=profile.id)


def ledger() -> IdentityStorageQuotaLedger:
    return IdentityStorageQuotaLedger()


def make_file(sessions, *, owner: UserProfile, name: str = "notes.txt") -> tuple:
    reader = reader_of(owner)
    with sessions() as session:
        directory = file_service.create_directory(
            session,
            owner_id=owner.id,
            path="docs",
            admission=RejectingScopeAdmission(),
            reader=reader,
            scope=ScopeRef(),
        )
        resource = file_service.create_file(
            session, owner_id=owner.id, directory_id=directory.id, name=name, reader=reader
        )
        session.commit()
    return directory, resource


def test_upload_versions_and_reads_back(database_sessions, tmp_path) -> None:
    owner = open_account(database_sessions, email="files@example.com", display_name="Files")
    _, resource = make_file(database_sessions, owner=owner)
    with database_sessions() as session:
        version = file_service.upload_version(
            session,
            file_id=resource.id,
            content=b"first",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="upload-1",
        )
        session.commit()
        assert version.version == 1
    with database_sessions() as session:
        second = file_service.upload_version(
            session,
            file_id=resource.id,
            content=b"second",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="upload-2",
        )
        session.commit()
        assert second.version == 2
    with database_sessions() as session:
        stored, payload = file_service.read_version_content(
            session,
            file_id=resource.id,
            version_number=2,
            reader=reader_of(owner),
            storage_root=tmp_path,
        )
        assert payload == b"second"
        assert stored.digest is not None


def test_a_repeated_idempotency_key_replays_the_version(database_sessions, tmp_path) -> None:
    owner = open_account(database_sessions, email="replay@example.com", display_name="Replay")
    _, resource = make_file(database_sessions, owner=owner)
    with database_sessions() as session:
        first = file_service.upload_version(
            session,
            file_id=resource.id,
            content=b"only once",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="replay-key",
        )
        session.commit()
    with database_sessions() as session:
        replayed = file_service.upload_version(
            session,
            file_id=resource.id,
            content=b"only once",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="replay-key",
        )
        session.commit()
        assert replayed.id == first.id
        assert file_service.latest_version_number(session, file_id=resource.id) == 1


def test_write_back_is_compare_and_set(database_sessions, tmp_path) -> None:
    owner = open_account(database_sessions, email="cas@example.com", display_name="Cas")
    _, resource = make_file(database_sessions, owner=owner)
    with database_sessions() as session:
        file_service.upload_version(
            session,
            file_id=resource.id,
            content=b"v1",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="cas-1",
        )
        session.commit()
    with database_sessions() as session, pytest.raises(ConflictError):
        file_service.write_back(
            session,
            file_id=resource.id,
            expected_version=7,
            content=b"stale writer",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="cas-stale",
        )
    with database_sessions() as session:
        version = file_service.write_back(
            session,
            file_id=resource.id,
            expected_version=1,
            content=b"v2",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="cas-2",
        )
        session.commit()
        assert version.version == 2


def test_grants_admit_the_grantee_and_nobody_else(database_sessions, tmp_path) -> None:
    owner = open_account(database_sessions, email="granter@example.com", display_name="Granter")
    grantee = open_account(database_sessions, email="grantee@example.com", display_name="Grantee")
    stranger = open_account(
        database_sessions, email="stranger@example.com", display_name="Stranger"
    )
    _, resource = make_file(database_sessions, owner=owner)
    with database_sessions() as session:
        file_service.grant_access(
            session,
            file_id=resource.id,
            grantee_id=grantee.id,
            permissions=["read"],
            reader=reader_of(owner),
            actor=actor_of(owner),
        )
        session.commit()
    with database_sessions() as session:
        file_service.upload_version(
            session,
            file_id=resource.id,
            content=b"shared",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="grant-upload",
        )
        session.commit()
    with database_sessions() as session:
        _, payload = file_service.read_version_content(
            session,
            file_id=resource.id,
            version_number=1,
            reader=reader_of(grantee),
            storage_root=tmp_path,
        )
        assert payload == b"shared"
        with pytest.raises(ForbiddenError):
            file_service.read_version_content(
                session,
                file_id=resource.id,
                version_number=1,
                reader=reader_of(stranger),
                storage_root=tmp_path,
            )
        with pytest.raises(ForbiddenError):
            file_service.upload_version(
                session,
                file_id=resource.id,
                content=b"write attempt",
                media_type="text/plain",
                reader=reader_of(grantee),
                actor=actor_of(grantee),
                ledger=ledger(),
                storage_root=tmp_path,
                idempotency_key="grant-write",
            )


def test_delete_removes_the_file_and_its_content(database_sessions, tmp_path) -> None:
    owner = open_account(database_sessions, email="delete@example.com", display_name="Delete")
    _, resource = make_file(database_sessions, owner=owner)
    with database_sessions() as session:
        version = file_service.upload_version(
            session,
            file_id=resource.id,
            content=b"doomed",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="delete-upload",
        )
        session.commit()
        content_path = tmp_path / content_storage_key(version.content_id)
        assert content_path.exists()
    with database_sessions() as session:
        file_service.delete_file(
            session, file_id=resource.id, reader=reader_of(owner), storage_root=tmp_path
        )
        session.commit()
        assert not content_path.exists()
    with database_sessions() as session:
        assert session.get(ResourceFile, resource.id) is None


def test_storage_reservation_is_settled_and_released_by_the_ledger(
    database_sessions, tmp_path
) -> None:
    owner = open_account(database_sessions, email="ledger@example.com", display_name="Ledger")
    _, resource = make_file(database_sessions, owner=owner)
    with database_sessions() as session:
        file_service.upload_version(
            session,
            file_id=resource.id,
            content=b"12345",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="ledger-upload",
        )
        session.commit()
    with database_sessions() as session:
        snapshot = quotas.get_quota(session, user_id=owner.id)
        assert snapshot.used_bytes == 5
        assert snapshot.reserved_bytes == 0


def test_quota_exceeded_refuses_without_storing_anything(database_sessions, tmp_path) -> None:
    owner = open_account(database_sessions, email="tight@example.com", display_name="Tight")
    with database_sessions() as session:
        quotas.ensure_ledger(session, user_id=owner.id)
        quotas.set_limits(
            session,
            user_id=owner.id,
            actor=AuditActor(actor_type="platform"),
            storage_limit_bytes=4,
        )
        session.commit()
    _, resource = make_file(database_sessions, owner=owner)
    with database_sessions() as session, pytest.raises(QuotaExceededError):
        file_service.upload_version(
            session,
            file_id=resource.id,
            content=b"far too large",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="tight-upload",
        )
    with database_sessions() as session:
        assert file_service.latest_version_number(session, file_id=resource.id) == 0
        assert not any(tmp_path.rglob("resource-contents/*"))


def test_a_ledger_failure_after_staging_removes_the_blob(database_sessions, tmp_path) -> None:
    """The staged blob dies with the transaction that staged it."""
    owner = open_account(database_sessions, email="boom@example.com", display_name="Boom")
    _, resource = make_file(database_sessions, owner=owner)

    class FailingLedger:
        def reserve_storage(self, session, *, user_id, amount, idempotency_scope, idempotency_key):
            return uuid4()

        def settle_storage(self, session, *, reservation_id, actual_amount):
            raise RuntimeError("the ledger is unavailable")

        def release_storage(self, session, *, reservation_id):
            return None

    with database_sessions() as session, pytest.raises(RuntimeError), session_transaction(session):
        file_service.upload_version(
            session,
            file_id=resource.id,
            content=b"staged then lost",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=FailingLedger(),
            storage_root=tmp_path,
            idempotency_key="boom-upload",
        )
    with database_sessions() as session:
        assert file_service.latest_version_number(session, file_id=resource.id) == 0
        assert not any(tmp_path.rglob("resource-contents/*"))


def test_staged_content_events_follow_the_transaction(database_sessions, tmp_path) -> None:
    """Rollback deletes staged blobs; commit keeps them."""
    content_id = new_content_id()
    from opsmesh.resources.storage.filesystem import write_content

    write_content(tmp_path, content_id=content_id, payload=b"staged")
    doomed_id = new_content_id()
    write_content(tmp_path, content_id=doomed_id, payload=b"kept-for-now")
    with database_sessions() as session, pytest.raises(RuntimeError), session_transaction(session):
        session.execute(text("select 1"))
        stage_content(session, tmp_path, content_id)
        stage_content_removal(session, tmp_path, doomed_id)
        raise RuntimeError("the transaction fails")
    assert not (tmp_path / content_storage_key(content_id)).exists()
    assert (tmp_path / content_storage_key(doomed_id)).exists()
    with database_sessions() as session, session_transaction(session):
        session.execute(text("select 1"))
        stage_content_removal(session, tmp_path, doomed_id)
    assert not (tmp_path / content_storage_key(doomed_id)).exists()


def test_runtime_policy_records_frozen_versions(database_sessions) -> None:
    owner = open_account(database_sessions, email="policy@example.com", display_name="Policy")
    _, resource = make_file(database_sessions, owner=owner)
    with database_sessions() as session, pytest.raises(InvalidArgumentError):
        file_service.set_runtime_policy(
            session,
            file_id=resource.id,
            frozen_input_versions=[9],
            relative_paths=["docs/notes.txt"],
            output_declarations=[],
            use_in_execution=True,
            reader=reader_of(owner),
            actor=actor_of(owner),
        )
    with database_sessions() as session:
        policy = file_service.set_runtime_policy(
            session,
            file_id=resource.id,
            frozen_input_versions=[],
            relative_paths=["docs/notes.txt"],
            output_declarations=[{"path": "out/report.md"}],
            use_in_execution=True,
            reader=reader_of(owner),
            actor=actor_of(owner),
        )
        session.commit()
        assert policy.use_in_execution is True
        assert policy.relative_paths == ["docs/notes.txt"]
    with database_sessions() as session, pytest.raises(InvalidArgumentError):
        file_service.set_runtime_policy(
            session,
            file_id=resource.id,
            frozen_input_versions=[],
            relative_paths=["../escape"],
            output_declarations=[],
            use_in_execution=False,
            reader=reader_of(owner),
            actor=actor_of(owner),
        )


def test_artifact_recording_uses_the_injected_authorization(database_sessions, tmp_path) -> None:
    owner = open_account(database_sessions, email="artifact@example.com", display_name="Artifact")
    with database_sessions() as session, pytest.raises(ForbiddenError):
        artifact_service.record_artifact(
            session,
            source_kind="run",
            source_id=uuid4(),
            owner_id=owner.id,
            name="report.md",
            content=b"artifact bytes",
            media_type="text/markdown",
            labels={},
            reader=reader_of(owner),
            actor=actor_of(owner),
            authorization=RejectingArtifactAuthorization(),
            storage_root=tmp_path,
        )

    class AllowingAuthorization:
        def authorize_record(self, *, reader, source_kind, source_id):
            return None

    source_id = uuid4()
    with database_sessions() as session:
        artifact = artifact_service.record_artifact(
            session,
            source_kind="run",
            source_id=source_id,
            owner_id=owner.id,
            name="report.md",
            content=b"artifact bytes",
            media_type="text/markdown",
            labels={"kind": "report"},
            reader=reader_of(owner),
            actor=actor_of(owner),
            authorization=AllowingAuthorization(),
            storage_root=tmp_path,
        )
        session.commit()
        artifact_id = artifact.id
    stranger = open_account(database_sessions, email="nosy@example.com", display_name="Nosy")
    with database_sessions() as session:
        listed = artifact_service.list_artifacts(session, reader=reader_of(owner))
        assert [item.id for item in listed] == [artifact_id]
        with pytest.raises(NotFoundError):
            artifact_service.get_artifact(
                session, artifact_id=artifact_id, reader=reader_of(stranger)
            )
        _, payload = artifact_service.read_artifact_content(
            session, artifact_id=artifact_id, reader=reader_of(owner), storage_root=tmp_path
        )
        assert payload == b"artifact bytes"
        with pytest.raises(InvalidArgumentError):
            artifact_service.record_artifact(
                session,
                source_kind="workflow",
                source_id=source_id,
                owner_id=owner.id,
                name="bad.md",
                content=b"x",
                media_type=None,
                labels={},
                reader=reader_of(owner),
                actor=actor_of(owner),
                authorization=AllowingAuthorization(),
                storage_root=tmp_path,
            )


def test_runtime_policy_refuses_unsafe_output_declarations(database_sessions) -> None:
    """An output declaration is a safe relative path no other file occupies.

    The policy becomes the input of a later write-back, so the service
    re-validates every declaration instead of trusting the request
    schema: a traversal or absolute path, an unknown field and a path
    another file already occupies are all refused before persisting.
    """
    owner = open_account(
        database_sessions, email="declarations@example.com", display_name="Declarations"
    )
    reader = reader_of(owner)
    with database_sessions() as session:
        directory = file_service.create_directory(
            session,
            owner_id=owner.id,
            path="docs",
            admission=RejectingScopeAdmission(),
            reader=reader,
            scope=ScopeRef(),
        )
        resource = file_service.create_file(
            session, owner_id=owner.id, directory_id=directory.id, name="notes.txt", reader=reader
        )
        sibling = file_service.create_file(
            session, owner_id=owner.id, directory_id=directory.id, name="report.md", reader=reader
        )
        session.commit()
    with database_sessions() as session:
        for declaration in ({"path": "../escape.md"}, {"path": "/etc/passwd"}):
            with pytest.raises(InvalidArgumentError):
                file_service.set_runtime_policy(
                    session,
                    file_id=resource.id,
                    frozen_input_versions=[],
                    relative_paths=[],
                    output_declarations=[declaration],
                    use_in_execution=True,
                    reader=reader_of(owner),
                    actor=actor_of(owner),
                )
        with pytest.raises(InvalidArgumentError):
            file_service.set_runtime_policy(
                session,
                file_id=resource.id,
                frozen_input_versions=[],
                relative_paths=[],
                output_declarations=[{"path": "out.md", "bogus": True}],
                use_in_execution=True,
                reader=reader_of(owner),
                actor=actor_of(owner),
            )
        with pytest.raises(ConflictError):
            file_service.set_runtime_policy(
                session,
                file_id=resource.id,
                frozen_input_versions=[],
                relative_paths=[],
                output_declarations=[{"path": "report.md"}],
                use_in_execution=True,
                reader=reader_of(owner),
                actor=actor_of(owner),
            )
        assert (
            file_service.get_runtime_policy(session, file_id=resource.id, reader=reader_of(owner))
            is None
        )
        policy = file_service.set_runtime_policy(
            session,
            file_id=resource.id,
            frozen_input_versions=[],
            relative_paths=["docs/notes.txt"],
            output_declarations=[{"path": "out/report.md", "media_type": "text/markdown"}],
            use_in_execution=True,
            reader=reader_of(owner),
            actor=actor_of(owner),
        )
        session.commit()
        assert policy.output_declarations == [
            {"path": "out/report.md", "media_type": "text/markdown", "description": ""}
        ]
        assert sibling.id != resource.id


TEST_CIPHER_KEY = "JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0="


def reset_tables(engine) -> None:
    """Start one concurrency test from a known-empty database."""
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE " + ", ".join(FEATURE_TABLES)))


@pytest.fixture
def clean_concurrency_tables(database_engine):
    """Empty the database around a concurrency test, before and after it."""
    reset_tables(database_engine)
    yield
    reset_tables(database_engine)


def concurrent_sessions(engine) -> sessionmaker[Session]:
    """One sessionmaker per worker thread over the shared test engine."""
    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        info={
            **transaction_dependencies(),
            "approvals": build_approvals(),
            "dispatch_cipher": SecretCipher(key=TEST_CIPHER_KEY, key_id="test"),
            "dispatch_trace_context": lambda: {},
        },
    )


def wait_for_lock_waits(engine, *, expected: int, timeout: float = 20.0) -> None:
    """Block until ``expected`` other backends wait for a lock."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND pid <> pg_backend_pid() "
                    "AND datname = current_database()"
                )
            )
        if (waiting or 0) >= expected:
            return
        time.sleep(0.02)
    raise TimeoutError("the expected lock waits did not appear in time")


def test_concurrent_write_back_conflicts_on_a_stale_expectation(
    database_engine, clean_concurrency_tables, tmp_path
) -> None:
    """Two writers of the same expected version cannot both pass the check.

    The interleaving is forced: the first writer replaces the content
    inside an open transaction, the second writer starts and blocks on
    the file row lock, and only then does the first commit. The second
    wakes up to the new version and is refused with a conflict; without
    the lock both pass from the same stale read and the database unique
    constraint decides the outcome.
    """
    sessions = concurrent_sessions(database_engine)
    owner = open_account(sessions, email="cas@example.com", display_name="Cas")
    _, resource = make_file(sessions, owner=owner)
    with sessions() as session, session.begin():
        file_service.upload_version(
            session,
            file_id=resource.id,
            content=b"first",
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key="cas-upload",
        )
    outcomes: dict[str, str] = {}

    def write_back(session: Session, content: bytes, key: str) -> None:
        file_service.write_back(
            session,
            file_id=resource.id,
            expected_version=1,
            content=content,
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key=key,
        )

    def attempt(name: str, content: bytes, key: str) -> None:
        try:
            with sessions() as session, session.begin():
                write_back(session, content, key)
            outcomes[name] = "written"
        except ConflictError:
            outcomes[name] = "conflicted"
        except Exception as exc:  # noqa: BLE001 - the loser must fail as a conflict
            outcomes[name] = type(exc).__name__

    holder = sessions()
    transaction = holder.begin()
    try:
        write_back(holder, b"written by the first writer", "cas-first")
        outcomes["first"] = "written"
        second = threading.Thread(
            target=attempt, args=("second", b"written by the second writer", "cas-second")
        )
        second.start()
        wait_for_lock_waits(database_engine, expected=1)
    finally:
        transaction.commit()
        holder.close()
    second.join(timeout=30)
    assert outcomes == {"first": "written", "second": "conflicted"}
    with sessions() as session:
        assert file_service.latest_version_number(session, file_id=resource.id) == 2


def test_concurrent_uploads_allocate_distinct_versions(
    database_engine, clean_concurrency_tables, tmp_path
) -> None:
    """Two uploads of one file cannot both claim the same version number.

    The interleaving is forced: the first upload stores version 1 inside
    an open transaction, the second upload starts and blocks on the file
    row lock, and only then does the first commit. Both writers then
    succeed with consecutive versions instead of racing toward the
    version unique constraint.
    """
    sessions = concurrent_sessions(database_engine)
    owner = open_account(sessions, email="upload-race@example.com", display_name="Upload")
    _, resource = make_file(sessions, owner=owner)
    outcomes: dict[str, str] = {}

    def upload(session: Session, content: bytes, key: str) -> None:
        file_service.upload_version(
            session,
            file_id=resource.id,
            content=content,
            media_type="text/plain",
            reader=reader_of(owner),
            actor=actor_of(owner),
            ledger=ledger(),
            storage_root=tmp_path,
            idempotency_key=key,
        )

    def attempt(name: str, content: bytes, key: str) -> None:
        try:
            with sessions() as session, session.begin():
                upload(session, content, key)
            outcomes[name] = "stored"
        except Exception as exc:  # noqa: BLE001 - both writers must succeed
            outcomes[name] = type(exc).__name__

    holder = sessions()
    transaction = holder.begin()
    try:
        upload(holder, b"stored by the first writer", "upload-race-first")
        outcomes["first"] = "stored"
        second = threading.Thread(
            target=attempt,
            args=("second", b"stored by the second writer", "upload-race-second"),
        )
        second.start()
        wait_for_lock_waits(database_engine, expected=1)
    finally:
        transaction.commit()
        holder.close()
    second.join(timeout=30)
    assert outcomes == {"first": "stored", "second": "stored"}
    with sessions() as session:
        assert file_service.latest_version_number(session, file_id=resource.id) == 2
