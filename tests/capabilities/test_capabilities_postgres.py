"""S5 capability tests against real PostgreSQL.

These tests require OPSMESH_TEST_DATABASE_URL; they are skipped otherwise and
must run against a real PostgreSQL instance (never SQLite). They cover the
acceptance surface of V4 section 16.2 for capabilities: public/private and
author metadata, enable/disable, immutable published versions, malicious
package and digest refusals, dependency resolution and ordering, and
idempotent provisioning of the bundled release.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.capabilities import lifecycle
from opsmesh.capabilities.models import (
    SOURCE_SYSTEM,
    VERSION_STATUS_DRAFT,
    VERSION_STATUS_PUBLISHED,
    CapabilityDefinition,
    CapabilityVersion,
)
from opsmesh.capabilities.packages import directory_digest, read_directory
from opsmesh.capabilities.provisioning import provision_release, read_release_index
from opsmesh.governance.audit.service import AuditActor
from opsmesh.identity.users import service as users
from opsmesh.identity.users.models import UserProfile
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError, NotFoundError
from opsmesh.shared.pagination import PageParams

BUNDLED_ROOT = Path(__file__).resolve().parents[2] / "bundled"


def open_account(
    sessions: sessionmaker[Session], *, email: str, display_name: str
) -> UserProfile:
    with sessions() as session:
        profile = users.create_user(
            session, email=email, display_name=display_name, actor=AuditActor(actor_type="platform")
        )
        session.commit()
    return profile


def create_expert(
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
        capability_type="expert",
        key=key,
        name=f"Expert {key}",
        description="An expert.",
        visibility=visibility,
        configuration={
            "instructions": instructions,
            "capability_slots": [],
            "parameters_schema": {"type": "object", "properties": {}},
            "secret_slots": [],
        },
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
        definition, draft = create_expert(session, author=author)
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
        definition, _ = create_expert(session, author=author)
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
            configuration={
                "instructions": "Revised instructions.",
                "capability_slots": [],
                "parameters_schema": {"type": "object", "properties": {}},
                "secret_slots": [],
            },
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
        assert stored.configuration["instructions"] == "Implement carefully."


def test_visibility_author_and_admin_scoping(database_sessions) -> None:
    author = open_account(database_sessions, email="owner@example.com", display_name="Owner")
    other = open_account(database_sessions, email="other@example.com", display_name="Other")
    with database_sessions() as session:
        private_definition, _ = create_expert(session, author=author, key="private-one")
        public_definition, _ = create_expert(
            session, author=author, key="public-one", visibility="public"
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
                configuration={"instructions": "hijack"},
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
        definition, _ = create_expert(session, author=author, key="disable-me", visibility="public")
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
            capability_type="team",
            key="pin-team",
            name="Pin team",
            description=None,
            visibility="public",
            configuration={
                "members": [{"slot": "lead", "expert_key": "disable-me", "role": "lead"}],
                "entrypoint": "lead",
                "handoffs": [],
                "agents_as_tools": [],
                "parameters_schema": {"type": "object", "properties": {}},
                "secret_slots": [],
            },
            dependencies=[{"type": "expert", "key": "disable-me", "version": 1}],
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
        dependency, _ = create_expert(
            session, author=author, key="private-dep", visibility="private"
        )
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
            capability_type="team",
            key="public-team",
            name="Public team",
            description=None,
            visibility="public",
            configuration={
                "members": [{"slot": "lead", "expert_key": "private-dep", "role": "lead"}],
                "entrypoint": "lead",
                "handoffs": [],
                "agents_as_tools": [],
                "parameters_schema": {"type": "object", "properties": {}},
                "secret_slots": [],
            },
            dependencies=[{"type": "expert", "key": "private-dep", "version": 1}],
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
        dependency, _ = create_expert(session, author=author, key="unpublished-dep")
        session.commit()
    with database_sessions() as session:
        team_definition, _ = lifecycle.create_definition(
            session,
            capability_type="team",
            key="waiting-team",
            name="Waiting team",
            description=None,
            visibility="private",
            configuration={
                "members": [{"slot": "lead", "expert_key": "unpublished-dep", "role": "lead"}],
                "entrypoint": "lead",
                "handoffs": [],
                "agents_as_tools": [],
                "parameters_schema": {"type": "object", "properties": {}},
                "secret_slots": [],
            },
            dependencies=[{"type": "expert", "key": "unpublished-dep", "version": 1}],
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


def test_provisioning_publishes_the_bundled_release_idempotently(
    database_sessions, tmp_path
) -> None:
    with database_sessions() as session:
        report = provision_release(
            session,
            release_root=BUNDLED_ROOT,
            storage_root=tmp_path,
            actor=AuditActor(actor_type="platform"),
        )
        session.commit()
    assert sorted(report.published) == ["coding", "development", "weather", "workspace-guide"]
    assert report.skipped == ()
    with database_sessions() as session:
        second = provision_release(
            session,
            release_root=BUNDLED_ROOT,
            storage_root=tmp_path,
            actor=AuditActor(actor_type="platform"),
        )
        session.commit()
    assert second.published == ()
    assert sorted(second.skipped) == ["coding", "development", "weather", "workspace-guide"]
    with database_sessions() as session:
        definition = session.scalar(
            select(CapabilityDefinition).where(
                CapabilityDefinition.type == "expert", CapabilityDefinition.key == "coding"
            )
        )
        assert definition is not None
        assert definition.source == SOURCE_SYSTEM
        assert definition.visibility == "public"
        with pytest.raises(ForbiddenError):
            lifecycle.update_draft(
                session,
                definition_id=definition.id,
                configuration={"instructions": "hijack"},
                actor=AuditActor(actor_type="user", actor_id=str(uuid4())),
                storage_root=tmp_path,
            )


def test_provisioning_refuses_a_tampered_release_digest(database_sessions, tmp_path) -> None:
    """A release package whose content changed is refused, never republished."""
    with database_sessions() as session:
        provision_release(
            session,
            release_root=BUNDLED_ROOT,
            storage_root=tmp_path,
            actor=AuditActor(actor_type="platform"),
        )
        session.commit()
    tampered = tmp_path / "tampered-release"
    copy_release(BUNDLED_ROOT, tampered)
    manifest_path = tampered / "experts" / "coding" / "AGENTS.md"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8"
    )
    with database_sessions() as session, pytest.raises(ConflictError):
        provision_release(
            session,
            release_root=tampered,
            storage_root=tmp_path,
            actor=AuditActor(actor_type="platform"),
        )


def test_provisioning_refuses_an_out_of_order_release(database_sessions, tmp_path) -> None:
    """A package that pins a not-yet-provisioned dependency breaks the order."""
    broken = tmp_path / "broken-release"
    copy_release(BUNDLED_ROOT, broken)
    index_path = broken / "manifest.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    team = next(entry for entry in index["packages"] if entry["key"] == "development")
    index["packages"].remove(team)
    index["packages"].insert(0, team)
    index_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    with database_sessions() as session, pytest.raises(ConflictError):
        provision_release(
            session,
            release_root=broken,
            storage_root=tmp_path,
            actor=AuditActor(actor_type="platform"),
        )


def copy_release(source: Path, target: Path) -> None:
    import shutil

    shutil.copytree(source, target)


def test_release_index_digests_match_the_bundled_content() -> None:
    _, entries = read_release_index(BUNDLED_ROOT)
    for entry in entries:
        files = read_directory(BUNDLED_ROOT / entry.path)
        assert directory_digest(files) == entry.digest
