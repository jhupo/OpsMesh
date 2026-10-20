"""S5 capability tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must run against a real PostgreSQL instance (never SQLite). They cover the
acceptance surface of V4 section 16.2 for capabilities: public/private and
author metadata, enable/disable, immutable published versions, malicious
package and digest refusals, and dependency resolution and ordering.
"""

from __future__ import annotations

import io
import threading
import time
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.bootstrap.approvals import build_approvals
from opsmesh.bootstrap.platform import transaction_dependencies
from opsmesh.capabilities import lifecycle
from opsmesh.capabilities.models import (
    VERSION_STATUS_DRAFT,
    VERSION_STATUS_PUBLISHED,
    CapabilityDefinition,
    CapabilityVersion,
)
from opsmesh.capabilities.packages import package_digest
from opsmesh.identity.users import service as users
from opsmesh.identity.users.models import UserProfile
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams
from opsmesh.shared.security.encryption import SecretCipher
from tests.conftest import FEATURE_TABLES


def open_account(sessions: sessionmaker[Session], *, email: str, display_name: str) -> UserProfile:
    with sessions() as session:
        profile = users.create_user(
            session, email=email, display_name=display_name, actor=AuditActor(actor_type="platform")
        )
        session.commit()
    return profile


def mcp_configuration(description: str) -> dict[str, object]:
    return {
        "transport": "remote",
        "endpoint": "https://example.com/mcp",
        "tools": [{"name": "invoke", "description": description}],
    }


def create_mcp(
    session: Session,
    *,
    author: UserProfile,
    key: str = "coding",
    instructions: str = "Implement carefully.",
    visibility: str = "private",
    dependencies: list[dict[str, object]] | None = None,
) -> tuple[CapabilityDefinition, CapabilityVersion]:
    return lifecycle.create_definition(
        session,
        capability_type="mcp",
        key=key,
        name=f"Expert {key}",
        description="An expert.",
        visibility=visibility,
        configuration=mcp_configuration(instructions),
        dependencies=dependencies or [],
        actor=AuditActor(actor_type="user", actor_id=str(author.id)),
        storage_root=Path(".opsmesh-test-capabilities"),
    )


def actor_of(profile: UserProfile) -> AuditActor:
    return AuditActor(actor_type="user", actor_id=str(profile.id))


def viewer_of(profile: UserProfile) -> lifecycle.Viewer:
    return lifecycle.Viewer(user_id=profile.id)


def test_create_publish_and_freeze(database_sessions, tmp_path) -> None:
    author = open_account(database_sessions, email="author@example.com", display_name="Author")
    with database_sessions() as session:
        definition, draft = create_mcp(session, author=author)
        assert draft.status == VERSION_STATUS_DRAFT
        assert draft.version == 1
        session.commit()
    with database_sessions() as session:
        published = lifecycle.publish_version(
            session,
            definition_id=definition.id,
            version_number=1,
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
        assert published.status == VERSION_STATUS_PUBLISHED
        assert published.digest is not None
    with database_sessions() as session, pytest.raises(ConflictError):
        lifecycle.publish_version(
            session,
            definition_id=definition.id,
            version_number=1,
            actor=actor_of(author),
            storage_root=tmp_path,
        )


def test_published_version_is_immutable_and_update_creates_a_new_draft(
    database_sessions, tmp_path
) -> None:
    author = open_account(database_sessions, email="author2@example.com", display_name="Author")
    with database_sessions() as session:
        definition, _ = create_mcp(session, author=author)
        lifecycle.publish_version(
            session,
            definition_id=definition.id,
            version_number=1,
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
    with database_sessions() as session:
        draft = lifecycle.update_draft(
            session,
            definition_id=definition.id,
            configuration=mcp_configuration("Revised instructions."),
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
        assert draft.version == 2
        assert draft.status == VERSION_STATUS_DRAFT
    with database_sessions() as session:
        stored = session.scalar(
            select(CapabilityVersion).where(
                CapabilityVersion.definition_id == definition.id,
                CapabilityVersion.version == 1,
            )
        )
        assert stored is not None
        assert stored.configuration["tools"][0]["description"] == "Implement carefully."


def test_visibility_author_and_admin_scoping(database_sessions) -> None:
    author = open_account(database_sessions, email="owner@example.com", display_name="Owner")
    other = open_account(database_sessions, email="other@example.com", display_name="Other")
    with database_sessions() as session:
        private_definition, _ = create_mcp(session, author=author, key="private-one")
        public_definition, _ = create_mcp(
            session, author=author, key="public-one", visibility="public"
        )
        lifecycle.publish_version(
            session,
            definition_id=public_definition.id,
            version_number=1,
            actor=actor_of(author),
            storage_root=Path(".opsmesh-test-capabilities"),
        )
        review = lifecycle.submit_publication_review(
            session,
            definition_id=public_definition.id,
            version_number=1,
            actor=actor_of(author),
            submission_note="",
        )
        lifecycle.decide_publication_review(
            session,
            review_id=review.id,
            actor=actor_of(other),
            actor_is_platform_admin=True,
            decision="approved",
            decision_note="",
        )
        session.commit()
        definitions, total = lifecycle.list_definitions(
            session, page=PageParams(limit=50, offset=0), viewer=viewer_of(other)
        )
        visible = {item.key for item in definitions}
        assert "public-one" in visible
        assert "private-one" not in visible
        assert total >= 1
        with pytest.raises(NotFoundError):
            lifecycle.get_definition(
                session, definition_id=private_definition.id, viewer=viewer_of(other)
            )
        with pytest.raises(ForbiddenError):
            lifecycle.update_draft(
                session,
                definition_id=private_definition.id,
                configuration=mcp_configuration("hijack"),
                actor=actor_of(other),
                storage_root=Path(".opsmesh-test-capabilities"),
            )
        # The author and a platform administrator both see the private one.
        admin_viewer = lifecycle.Viewer(user_id=other.id, is_platform_admin=True)
        assert (
            lifecycle.get_definition(
                session, definition_id=private_definition.id, viewer=admin_viewer
            ).id
            == private_definition.id
        )


def test_disable_blocks_publish_and_delete_checks_references(database_sessions, tmp_path) -> None:
    author = open_account(database_sessions, email="lifecycle@example.com", display_name="Life")
    with database_sessions() as session:
        definition, _ = create_mcp(session, author=author, key="disable-me", visibility="public")
        session.commit()
    with database_sessions() as session:
        lifecycle.set_definition_status(
            session,
            definition_id=definition.id,
            status="disabled",
            actor=actor_of(author),
            actor_is_platform_admin=False,
        )
        session.commit()
        with pytest.raises(ConflictError):
            lifecycle.publish_version(
                session,
                definition_id=definition.id,
                version_number=1,
                actor=actor_of(author),
                storage_root=tmp_path,
            )
    with database_sessions() as session:
        lifecycle.set_definition_status(
            session,
            definition_id=definition.id,
            status="active",
            actor=actor_of(author),
            actor_is_platform_admin=False,
        )
        lifecycle.publish_version(
            session,
            definition_id=definition.id,
            version_number=1,
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
    # A team pins the published expert; deleting the expert must be refused.
    with database_sessions() as session:
        team_definition, _ = lifecycle.create_definition(
            session,
            capability_type="mcp",
            key="pin-team",
            name="Pin team",
            description=None,
            visibility="public",
            configuration=mcp_configuration("Dependency carrier"),
            dependencies=[{"type": "mcp", "key": "disable-me", "version": 1}],
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
        with pytest.raises(ConflictError):
            lifecycle.set_definition_status(
                session,
                definition_id=definition.id,
                status="deleted",
                actor=actor_of(author),
                actor_is_platform_admin=False,
            )


def test_public_definition_cannot_pin_a_private_dependency(database_sessions, tmp_path) -> None:
    author = open_account(database_sessions, email="pin@example.com", display_name="Pin")
    with database_sessions() as session:
        dependency, _ = create_mcp(session, author=author, key="private-dep", visibility="private")
        session.commit()
    with database_sessions() as session:
        lifecycle.publish_version(
            session,
            definition_id=dependency.id,
            version_number=1,
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
    with database_sessions() as session:
        team_definition, _ = lifecycle.create_definition(
            session,
            capability_type="mcp",
            key="public-team",
            name="Public team",
            description=None,
            visibility="public",
            configuration=mcp_configuration("Dependency carrier"),
            dependencies=[{"type": "mcp", "key": "private-dep", "version": 1}],
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
        with pytest.raises(ConflictError):
            lifecycle.publish_version(
                session,
                definition_id=team_definition.id,
                version_number=1,
                actor=actor_of(author),
                storage_root=tmp_path,
            )


def test_publish_refuses_an_unpublished_dependency(database_sessions, tmp_path) -> None:
    author = open_account(database_sessions, email="unpub@example.com", display_name="Unpub")
    with database_sessions() as session:
        dependency, _ = create_mcp(session, author=author, key="unpublished-dep")
        session.commit()
    with database_sessions() as session:
        team_definition, _ = lifecycle.create_definition(
            session,
            capability_type="mcp",
            key="waiting-team",
            name="Waiting team",
            description=None,
            visibility="private",
            configuration=mcp_configuration("Dependency carrier"),
            dependencies=[{"type": "mcp", "key": "unpublished-dep", "version": 1}],
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
        with pytest.raises(ConflictError):
            lifecycle.publish_version(
                session,
                definition_id=team_definition.id,
                version_number=1,
                actor=actor_of(author),
                storage_root=tmp_path,
            )


def test_malicious_package_is_refused_before_anything_is_stored(
    database_sessions, tmp_path
) -> None:
    author = open_account(database_sessions, email="evil@example.com", display_name="Evil")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.txt", b"owned")
    with database_sessions() as session:
        with pytest.raises(InvalidArgumentError):
            lifecycle.create_definition(
                session,
                capability_type="skill",
                key="evil-skill",
                name="Evil",
                description=None,
                visibility="private",
                configuration=None,
                actor=actor_of(author),
                storage_root=tmp_path,
                package=buffer.getvalue(),
                filename="evil.zip",
            )
        stored = session.scalar(
            select(CapabilityDefinition).where(CapabilityDefinition.key == "evil-skill")
        )
        assert stored is None


def test_skill_import_roundtrip_and_package_download(database_sessions, tmp_path) -> None:
    author = open_account(database_sessions, email="skill@example.com", display_name="Skill")
    payload = build_skill_zip()
    with database_sessions() as session:
        definition, draft = lifecycle.create_definition(
            session,
            capability_type="skill",
            key="workspace-guide",
            name="Workspace guide",
            description=None,
            visibility="public",
            configuration=None,
            actor=actor_of(author),
            storage_root=tmp_path,
            package=payload,
            filename="skill.zip",
        )
        session.commit()
        assert draft.configuration["frontmatter"]["name"] == "workspace-guide"
    with database_sessions() as session:
        published = lifecycle.publish_version(
            session,
            definition_id=definition.id,
            version_number=1,
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
        assert published.package_digest is not None
    with database_sessions() as session:
        stored, downloaded = lifecycle.read_version_package(
            session, version_id=published.id, storage_root=tmp_path
        )
        assert downloaded == payload
        assert stored.filename == "skill.zip"


def build_skill_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "SKILL.md",
            b"---\nname: workspace-guide\ndescription: A guide.\n---\n\nBody.\n",
        )
        archive.writestr("references/extra.md", b"extra")
    return buffer.getvalue()


def channel_configuration() -> dict[str, object]:
    return {
        "protocol": "wecom",
        "operations": [
            {"name": "receive", "entrypoint": "wecom.adapter:receive"},
            {"name": "send", "entrypoint": "wecom.adapter:send"},
        ],
    }


def build_channel_zip(adapter: bytes = b"# the adapter stays data at publish\n") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("wecom/adapter.py", adapter)
    return buffer.getvalue()


def channel_manifest(adapter: bytes) -> list[dict[str, object]]:
    return [{"path": "wecom/adapter.py", "digest": package_digest(adapter), "size": len(adapter)}]


def extension_configuration() -> dict[str, object]:
    return {
        "operations": [
            {
                "export_id": "summarize",
                "description": "Summarize one run.",
                "effects": ["read"],
                "permissions": ["platform.audit.append"],
            }
        ],
        "hooks": [
            {
                "export_id": "guard-output",
                "description": "Guard model output.",
                "stages": ["output_guardrail"],
                "failure_policy": "block",
            }
        ],
        "events": [
            {
                "export_id": "on-run-finished",
                "description": "Consume finished runs.",
                "source_types": ["run"],
                "source_versions": ["1"],
                "operation_export_id": "summarize",
                "idempotency_key_fields": ["run_id"],
            }
        ],
        "permissions": ["platform.audit.append"],
    }


def build_extension_zip(code: bytes = b"# extension code stays data at publish\n") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ext/main.py", code)
    return buffer.getvalue()


def extension_manifest(code: bytes) -> list[dict[str, object]]:
    return [{"path": "ext/main.py", "digest": package_digest(code), "size": len(code)}]


def test_channel_definition_publishes_with_its_adapter_package(database_sessions, tmp_path) -> None:
    author = open_account(database_sessions, email="channel@example.com", display_name="Channel")
    adapter = b"# the adapter stays data at publish\n"
    payload = build_channel_zip(adapter)
    with database_sessions() as session:
        definition, draft = lifecycle.create_definition(
            session,
            capability_type="channel",
            key="wecom",
            name="WeCom channel",
            description="Enterprise WeChat channel.",
            visibility="private",
            configuration=channel_configuration(),
            resource_manifest=channel_manifest(adapter),
            actor=actor_of(author),
            storage_root=tmp_path,
            package=payload,
            filename="channel.zip",
        )
        session.commit()
        assert draft.version == 1
        assert draft.package_digest is not None
    with database_sessions() as session:
        published = lifecycle.publish_version(
            session,
            definition_id=definition.id,
            version_number=1,
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
        assert published.status == VERSION_STATUS_PUBLISHED
        assert published.digest is not None
    with database_sessions() as session:
        stored, downloaded = lifecycle.read_version_package(
            session, version_id=published.id, storage_root=tmp_path
        )
        assert downloaded == payload
        assert stored.filename == "channel.zip"


def test_channel_publish_requires_the_adapter_package(database_sessions, tmp_path) -> None:
    author = open_account(database_sessions, email="channel-bare@example.com", display_name="Bare")
    with database_sessions() as session:
        definition, _ = lifecycle.create_definition(
            session,
            capability_type="channel",
            key="package-less",
            name="WeCom channel",
            description=None,
            visibility="private",
            configuration=channel_configuration(),
            resource_manifest=[],
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
        with pytest.raises(ConflictError):
            lifecycle.publish_version(
                session,
                definition_id=definition.id,
                version_number=1,
                actor=actor_of(author),
                storage_root=tmp_path,
            )


def test_channel_package_must_declare_its_entrypoint_module(database_sessions, tmp_path) -> None:
    author = open_account(
        database_sessions, email="channel-entry@example.com", display_name="Entry"
    )
    adapter = b"# the adapter stays data at publish\n"
    configuration = channel_configuration()
    configuration["operations"][0]["entrypoint"] = "other.adapter:receive"
    with database_sessions() as session:
        with pytest.raises(InvalidArgumentError):
            lifecycle.create_definition(
                session,
                capability_type="channel",
                key="undeclared-entrypoint",
                name="WeCom channel",
                description=None,
                visibility="private",
                configuration=configuration,
                resource_manifest=channel_manifest(adapter),
                actor=actor_of(author),
                storage_root=tmp_path,
                package=build_channel_zip(adapter),
                filename="channel.zip",
            )
        stored = session.scalar(
            select(CapabilityDefinition).where(CapabilityDefinition.key == "undeclared-entrypoint")
        )
        assert stored is None


def test_extension_definition_publishes_with_its_package(database_sessions, tmp_path) -> None:
    author = open_account(
        database_sessions, email="extension@example.com", display_name="Extension"
    )
    code = b"# extension code stays data at publish\n"
    payload = build_extension_zip(code)
    with database_sessions() as session:
        definition, draft = lifecycle.create_definition(
            session,
            capability_type="extension",
            key="run-digest",
            name="Run digest",
            description="Digests finished runs.",
            visibility="private",
            configuration=extension_configuration(),
            resource_manifest=extension_manifest(code),
            actor=actor_of(author),
            storage_root=tmp_path,
            package=payload,
            filename="extension.zip",
        )
        session.commit()
        assert draft.version == 1
        assert draft.package_digest is not None
    with database_sessions() as session:
        published = lifecycle.publish_version(
            session,
            definition_id=definition.id,
            version_number=1,
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
        assert published.status == VERSION_STATUS_PUBLISHED
        assert published.digest is not None
    with database_sessions() as session:
        stored, downloaded = lifecycle.read_version_package(
            session, version_id=published.id, storage_root=tmp_path
        )
        assert downloaded == payload
        assert stored.filename == "extension.zip"


def test_extension_publish_requires_the_package(database_sessions, tmp_path) -> None:
    author = open_account(
        database_sessions, email="extension-bare@example.com", display_name="Bare ext"
    )
    with database_sessions() as session:
        definition, _ = lifecycle.create_definition(
            session,
            capability_type="extension",
            key="package-less",
            name="Run digest",
            description=None,
            visibility="private",
            configuration=extension_configuration(),
            resource_manifest=[],
            actor=actor_of(author),
            storage_root=tmp_path,
        )
        session.commit()
        with pytest.raises(ConflictError):
            lifecycle.publish_version(
                session,
                definition_id=definition.id,
                version_number=1,
                actor=actor_of(author),
                storage_root=tmp_path,
            )


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


def test_concurrent_definition_creation_conflicts_on_one_key(
    database_engine, clean_concurrency_tables, tmp_path
) -> None:
    """Two creators of one type and key cannot both succeed.

    The interleaving is forced, not raced: the first creator holds its
    transaction open with the definition row already inserted, the second
    creator starts and blocks on the unique index, and only then does the
    first commit. The loser must surface as a conflict instead of a
    database error, whichever side of the commit it wakes up on.
    """
    sessions = concurrent_sessions(database_engine)
    author = open_account(sessions, email="race@example.com", display_name="Race")
    adapter = b"# the adapter stays data at publish\n"
    outcomes: dict[str, str] = {}

    def create(session: Session) -> None:
        lifecycle.create_definition(
            session,
            capability_type="channel",
            key="wecom",
            name="WeCom channel",
            description=None,
            visibility="private",
            configuration=channel_configuration(),
            resource_manifest=channel_manifest(adapter),
            actor=actor_of(author),
            storage_root=tmp_path,
            package=build_channel_zip(adapter),
            filename="channel.zip",
        )

    def attempt(name: str) -> None:
        try:
            with sessions() as session, session.begin():
                create(session)
            outcomes[name] = "created"
        except ConflictError:
            outcomes[name] = "refused"
        except Exception as exc:  # noqa: BLE001 - the loser must fail as a conflict
            outcomes[name] = type(exc).__name__

    holder = sessions()
    transaction = holder.begin()
    try:
        create(holder)
        outcomes["first"] = "created"
        second = threading.Thread(target=attempt, args=("second",))
        second.start()
        wait_for_lock_waits(database_engine, expected=1)
    finally:
        transaction.commit()
        holder.close()
    second.join(timeout=30)
    assert outcomes == {"first": "created", "second": "refused"}


def test_concurrent_publish_publishes_one_version(
    database_engine, clean_concurrency_tables, tmp_path
) -> None:
    """Two publishers of one draft cannot both freeze it.

    The interleaving is forced: the first publisher flips the version
    inside an open transaction, the second publisher starts and blocks on
    the definition row lock, and only then does the first commit. The
    second transaction wakes up to an already published version and is
    refused; without the row lock both freeze the same content.
    """
    sessions = concurrent_sessions(database_engine)
    author = open_account(sessions, email="publish-race@example.com", display_name="Publish")
    with sessions() as session, session.begin():
        definition, _ = create_mcp(session, author=author, key="race-publish")
    outcomes: dict[str, str] = {}

    def publish(session: Session) -> None:
        lifecycle.publish_version(
            session,
            definition_id=definition.id,
            version_number=1,
            actor=actor_of(author),
            storage_root=tmp_path,
        )

    def attempt(name: str) -> None:
        try:
            with sessions() as session, session.begin():
                publish(session)
            outcomes[name] = "published"
        except ConflictError:
            outcomes[name] = "refused"
        except Exception as exc:  # noqa: BLE001 - the loser must fail as a conflict
            outcomes[name] = type(exc).__name__

    holder = sessions()
    transaction = holder.begin()
    try:
        publish(holder)
        outcomes["first"] = "published"
        second = threading.Thread(target=attempt, args=("second",))
        second.start()
        wait_for_lock_waits(database_engine, expected=1)
    finally:
        transaction.commit()
        holder.close()
    second.join(timeout=30)
    assert outcomes == {"first": "published", "second": "refused"}
    with sessions() as session:
        version = session.scalar(
            select(CapabilityVersion).where(
                CapabilityVersion.definition_id == definition.id,
                CapabilityVersion.version == 1,
            )
        )
        assert version is not None
        assert version.status == VERSION_STATUS_PUBLISHED


def test_concurrent_draft_updates_after_publish_keep_one_draft(
    database_engine, clean_concurrency_tables, tmp_path
) -> None:
    """Two draft replacements after a publish allocate one new version.

    The interleaving is forced: the first replacement creates version 2
    inside an open transaction, the second replacement starts and blocks
    on the definition row lock, and only then does the first commit. The
    second then applies its content to that same draft instead of racing
    toward the version unique constraint.
    """
    sessions = concurrent_sessions(database_engine)
    author = open_account(sessions, email="draft-race@example.com", display_name="Draft")
    with sessions() as session, session.begin():
        definition, _ = create_mcp(session, author=author, key="race-draft")
        lifecycle.publish_version(
            session,
            definition_id=definition.id,
            version_number=1,
            actor=actor_of(author),
            storage_root=tmp_path,
        )
    outcomes: dict[str, str] = {}

    def update(session: Session, instructions: str) -> None:
        lifecycle.update_draft(
            session,
            definition_id=definition.id,
            configuration=mcp_configuration(instructions),
            actor=actor_of(author),
            storage_root=Path(".opsmesh-test-capabilities"),
        )

    def attempt(name: str, instructions: str) -> None:
        try:
            with sessions() as session, session.begin():
                update(session, instructions)
            outcomes[name] = "updated"
        except Exception as exc:  # noqa: BLE001 - both writers must succeed
            outcomes[name] = type(exc).__name__

    holder = sessions()
    transaction = holder.begin()
    try:
        update(holder, "Revised by the first writer.")
        outcomes["first"] = "updated"
        second = threading.Thread(target=attempt, args=("second", "Revised by the second writer."))
        second.start()
        wait_for_lock_waits(database_engine, expected=1)
    finally:
        transaction.commit()
        holder.close()
    second.join(timeout=30)
    assert outcomes == {"first": "updated", "second": "updated"}
    with sessions() as session:
        versions = list(
            session.scalars(
                select(CapabilityVersion)
                .where(CapabilityVersion.definition_id == definition.id)
                .order_by(CapabilityVersion.version)
            )
        )
        assert [item.version for item in versions] == [1, 2]
        assert versions[0].status == VERSION_STATUS_PUBLISHED
        assert versions[1].status == VERSION_STATUS_DRAFT
