from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.files.models import WorkspaceFile
from backend.app.files.runtime_files import RuntimeFileService
from backend.app.files.storage import LocalStorage
from backend.app.identity.models import User
from backend.app.tools.context import ToolContext
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_runtime_file_staging_and_artifact_collection(tmp_path: Path) -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    storage = LocalStorage(str(tmp_path / "storage"))
    storage_key = f"workspaces/{workspace.id}/files/demo/input.txt"
    storage.write(storage_key, b"hello")
    file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=user.id,
        filename="input.txt",
        content_type="text/plain",
        size_bytes=5,
        checksum_sha256="a" * 64,
        storage_key=storage_key,
    )
    session.add(file)
    session.commit()
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=None,
        agent_run_id=None,
        allowed_tools=frozenset({"read_workspace_file", "write_artifact"}),
    )
    service = RuntimeFileService(session, storage, str(tmp_path / "runtime"))

    staged = service.stage_workspace_file(
        context=context,
        file_id=file.id,
        relative_path="inputs/input.txt",
    )
    artifact = service.collect_artifact(
        context=context,
        filename="output.txt",
        content=b"done",
        content_type="text/plain",
    )

    assert staged.read_bytes() == b"hello"
    assert artifact.workspace_id == workspace.id
    assert artifact.size_bytes == 4


def test_runtime_staging_rejects_path_escape(tmp_path: Path) -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    storage = LocalStorage(str(tmp_path / "storage"))
    file = WorkspaceFile(
        workspace_id=workspace.id,
        uploaded_by_user_id=user.id,
        filename="input.txt",
        content_type="text/plain",
        size_bytes=5,
        checksum_sha256="a" * 64,
        storage_key=f"workspaces/{workspace.id}/files/demo/input.txt",
    )
    session.add(file)
    session.commit()
    context = ToolContext(
        workspace_id=workspace.id,
        task_id=None,
        agent_run_id=None,
        allowed_tools=frozenset({"read_workspace_file"}),
    )
    service = RuntimeFileService(session, storage, str(tmp_path / "runtime"))

    try:
        service.stage_workspace_file(context=context, file_id=file.id, relative_path="../bad.txt")
    except ValueError as exc:
        assert "escapes" in str(exc)
    else:
        raise AssertionError("Expected path escape to fail")


def test_local_storage_rejects_unsafe_keys(tmp_path: Path) -> None:
    storage = LocalStorage(str(tmp_path / "storage"))

    for key in ["../escape.txt", "/absolute.txt", "workspaces\\bad\\file.txt"]:
        try:
            storage.write(key, b"bad")
        except ValueError as exc:
            assert "Storage key" in str(exc)
        else:
            raise AssertionError("Expected unsafe storage key to fail")


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_workspace(session: Session) -> tuple[User, Workspace]:
    user = User(email=f"{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name="Acme", slug=str(uuid4()), settings={})
    membership = WorkspaceMember(workspace=workspace, user=user, role="owner")
    session.add_all([user, workspace, membership])
    session.commit()
    return user, workspace


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
