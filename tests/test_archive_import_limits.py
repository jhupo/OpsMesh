"""Untrusted archives stay bounded and imported team invariants match normal writes."""

import json
from hashlib import sha256
from io import BytesIO
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from sqlalchemy import select

from opsmesh.agents.profiles.models import AgentProfile
from opsmesh.resources.transfers.archive_limits import (
    MAX_ARCHIVE_INVENTORY_BYTES,
    MAX_ARCHIVE_MEMBERS,
    MAX_ARCHIVE_METADATA_BYTES,
    BoundedArchiveReader,
    compressed_archive_limit,
)
from opsmesh.resources.transfers.contracts import (
    WorkspaceArchiveImportRequest,
    WorkspaceImportResponse,
)
from opsmesh.resources.transfers.repository import WorkspaceArchiveBlobReader
from opsmesh.teams.management.models import AgentTeam, AgentTeamMember
from tests.test_workspace_export_api import _client, _headers, _seed_workspace


def _zip(members: dict[str, bytes]) -> bytes:
    content = BytesIO()
    with ZipFile(content, "w", compression=ZIP_DEFLATED) as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return content.getvalue()


@pytest.mark.parametrize("dry_run", [True, False])
def test_archive_import_rejects_old_runtime_metadata_format_before_writes(tmp_path, dry_run):
    client, session = _client(tmp_path)
    source_user, source = _seed_workspace(
        session, email="old-format@example.com", slug="old-format"
    )
    user, target = _seed_workspace(session, email="target-format@example.com", slug="target-format")
    session.add(AgentTeam(workspace_id=source.id, name="Source team", team_type="general"))
    session.commit()
    exported = client.post(
        f"/api/v1/workspaces/{source.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={"include_audit_events": False},
    )
    assert exported.status_code == 200
    metadata = exported.json()
    metadata["manifest"]["format_version"] = "workspace-export.v2"
    response = client.post(
        f"/api/v1/workspaces/{target.id}/exports/archive/import",
        headers=_headers(user.id),
        files={
            "file": (
                "old.zip",
                _zip(
                    {"metadata.json": json.dumps(metadata).encode(), "object-inventory.json": b"[]"}
                ),
                "application/zip",
            )
        },
        data={"dry_run": str(dry_run).lower()},
    )
    assert response.status_code == 400
    assert "Unsupported workspace archive format" in response.text
    assert session.scalars(select(AgentTeam).where(AgentTeam.workspace_id == target.id)).all() == []


@pytest.mark.parametrize("member", ["metadata.json", "object-inventory.json"])
def test_archive_import_rejects_compressed_metadata_bombs_before_writes(tmp_path, member):
    client, session = _client(tmp_path)
    user, workspace = _seed_workspace(session, email="limits@example.com", slug="limits")
    exported = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/metadata",
        headers=_headers(user.id),
        json={"include_audit_events": False},
    )
    assert exported.status_code == 200
    members = {"metadata.json": exported.content, "object-inventory.json": b"[]"}
    limit = MAX_ARCHIVE_METADATA_BYTES if member == "metadata.json" else MAX_ARCHIVE_INVENTORY_BYTES
    members[member] = b" " * (limit + 1)
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/import",
        headers=_headers(user.id),
        files={"file": ("bomb.zip", _zip(members), "application/zip")},
        data={"dry_run": "false"},
    )
    assert response.status_code == 400
    assert "byte limit" in response.text
    assert (
        session.scalars(select(AgentTeam).where(AgentTeam.workspace_id == workspace.id)).all() == []
    )


def test_archive_upload_rejects_compressed_bytes_before_zip_parsing(tmp_path):
    client, session = _client(tmp_path)
    user, workspace = _seed_workspace(session, email="upload-limit@example.com", slug="upload")
    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/exports/archive/import/preview",
        headers=_headers(user.id),
        files={"file": ("large.zip", b"x" * (compressed_archive_limit(1) + 1), "application/zip")},
        data={"max_total_bytes": "1"},
    )
    assert response.status_code == 413


def test_archive_reader_rejects_excessive_members_and_duplicate_names():
    content = BytesIO()
    with ZipFile(content, "w") as archive:
        for index in range(MAX_ARCHIVE_MEMBERS + 1):
            archive.writestr(str(index), b"")
    with (
        ZipFile(BytesIO(content.getvalue())) as archive,
        pytest.raises(ValueError, match="too many members"),
    ):
        BoundedArchiveReader(archive, max_total_bytes=10)
    content = BytesIO()
    with ZipFile(content, "w") as archive:
        archive.writestr("metadata.json", b"one")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("metadata.json", b"two")
    with (
        ZipFile(BytesIO(content.getvalue())) as archive,
        pytest.raises(ValueError, match="duplicate member"),
    ):
        BoundedArchiveReader(archive, max_total_bytes=10)


def test_failed_checksum_reads_still_consume_archive_decompression_budget():
    response = WorkspaceImportResponse(
        dry_run=True,
        source_workspace_id=uuid4(),
        target_workspace_id=uuid4(),
        created_counts={"files": 0},
        skipped_counts={"files": 0},
        id_map={},
        warnings=[],
        conflict_plan=[],
    )
    request = WorkspaceArchiveImportRequest(max_bytes_per_object=4, max_total_bytes=4)
    with ZipFile(BytesIO(_zip({"first": b"aaaa", "second": b"bbbb"}))) as archive:
        reader = WorkspaceArchiveBlobReader(archive, max_total_bytes=request.max_total_bytes)
        for name, expected in (
            ("first", sha256(b"wrong").hexdigest()),
            ("second", sha256(b"bbbb").hexdigest()),
        ):
            assert (
                reader.read_blob(
                    archive_name=name,
                    source_id=name,
                    collection="files",
                    response=response,
                    request=request,
                    expected_checksum=expected,
                )
                is None
            )
    assert [conflict.field for conflict in response.conflict_plan] == [
        "checksum_sha256",
        "total_bytes",
    ]


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("invalid", ["secret", "cross_team", "cycle"])
def test_team_metadata_import_rejects_secrets_and_invalid_reporting_without_partial_writes(
    tmp_path, dry_run, invalid
):
    client, session = _client(tmp_path)
    source_user, source = _seed_workspace(session, email="source-team@example.com", slug="source")
    user, target = _seed_workspace(session, email="target-team@example.com", slug="target")
    agents = [
        AgentProfile(workspace_id=source.id, name=f"agent-{index}", role="worker")
        for index in range(2)
    ]
    teams = [
        AgentTeam(workspace_id=source.id, name=f"team-{index}", team_type="general")
        for index in range(2)
    ]
    session.add_all([*agents, *teams])
    session.flush()
    members = [
        AgentTeamMember(
            workspace_id=source.id,
            agent_team_id=teams[0].id,
            agent_profile_id=agent.id,
            team_role="worker",
        )
        for agent in agents
    ]
    session.add_all(members)
    session.commit()
    exported = client.post(
        f"/api/v1/workspaces/{source.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={"include_audit_events": False},
    )
    assert exported.status_code == 200
    payload = json.loads(exported.content)
    by_id = {item["id"]: item for item in payload["team_members"]}
    if invalid == "secret":
        payload["teams"][0]["coordination_rules"] = {"api_key": "import-secret-canary"}
    elif invalid == "cross_team":
        by_id[str(members[0].id)]["reports_to_member_id"] = str(members[1].id)
        by_id[str(members[1].id)]["agent_team_id"] = str(teams[1].id)
    else:
        by_id[str(members[0].id)]["reports_to_member_id"] = str(members[1].id)
        by_id[str(members[1].id)]["reports_to_member_id"] = str(members[0].id)
    response = client.post(
        f"/api/v1/workspaces/{target.id}/exports/metadata/import",
        headers=_headers(user.id),
        json={"export": payload, "dry_run": dry_run},
    )
    assert response.status_code == 400
    assert "import-secret-canary" not in response.text
    for model in (AgentProfile, AgentTeam, AgentTeamMember):
        assert session.scalars(select(model).where(model.workspace_id == target.id)).all() == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_team_import_rejects_existing_manager_from_another_team(tmp_path, dry_run):
    client, session = _client(tmp_path)
    source_user, source = _seed_workspace(
        session, email="source-existing@example.com", slug="source"
    )
    user, target = _seed_workspace(session, email="target-existing@example.com", slug="target")
    source_agent = AgentProfile(workspace_id=source.id, name="source-agent", role="worker")
    source_team = AgentTeam(workspace_id=source.id, name="source-team", team_type="general")
    target_agent = AgentProfile(workspace_id=target.id, name="target-agent", role="worker")
    target_team = AgentTeam(workspace_id=target.id, name="target-team", team_type="general")
    session.add_all([source_agent, source_team, target_agent, target_team])
    session.flush()
    source_member = AgentTeamMember(
        workspace_id=source.id,
        agent_team_id=source_team.id,
        agent_profile_id=source_agent.id,
        team_role="worker",
    )
    target_member = AgentTeamMember(
        workspace_id=target.id,
        agent_team_id=target_team.id,
        agent_profile_id=target_agent.id,
        team_role="worker",
    )
    session.add_all([source_member, target_member])
    session.commit()
    exported = client.post(
        f"/api/v1/workspaces/{source.id}/exports/metadata",
        headers=_headers(source_user.id),
        json={"include_audit_events": False},
    )
    payload = exported.json()
    missing_parent = str(uuid4())
    payload["team_members"][0]["reports_to_member_id"] = missing_parent
    response = client.post(
        f"/api/v1/workspaces/{target.id}/exports/metadata/import",
        headers=_headers(user.id),
        json={
            "export": payload,
            "dry_run": dry_run,
            "resolutions": {
                f"team_members:{source_member.id}": {
                    "action": "import_dependency",
                    "dependencies": {"reports_to_member_id": str(target_member.id)},
                }
            },
        },
    )
    assert response.status_code == 400
    assert "Reporting manager" in response.text
    session.expire_all()
    assert [
        team.id
        for team in session.scalars(select(AgentTeam).where(AgentTeam.workspace_id == target.id))
    ] == [target_team.id]
    assert session.get(AgentTeamMember, UUID(str(target_member.id))).reports_to_member_id is None
