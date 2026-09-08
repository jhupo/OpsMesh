from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from backend.app.files.content import WorkspaceFileContentReader, WorkspaceFileReadError
from backend.app.files.models import WorkspaceFile
from backend.app.files.storage import LocalStorage


def test_agent_file_reader_rejects_type_and_declared_or_actual_oversize(
    tmp_path: Path,
) -> None:
    workspace_id = uuid4()
    storage = LocalStorage(str(tmp_path / "storage"))
    reader = WorkspaceFileContentReader(storage, max_bytes=5)

    unsupported = _file(
        workspace_id,
        filename="report.pdf",
        content_type="application/pdf",
        content=b"pdf",
    )
    declared_oversize = _file(
        workspace_id,
        filename="large.txt",
        content_type="text/plain",
        content=b"123456",
    )
    actual_oversize = _file(
        workspace_id,
        filename="mismatch.txt",
        content_type="text/plain",
        content=b"12345",
    )
    storage.write(unsupported.storage_key, b"pdf")
    storage.write(declared_oversize.storage_key, b"123456")
    storage.write(actual_oversize.storage_key, b"123456")

    with pytest.raises(WorkspaceFileReadError) as unsupported_error:
        reader.read(unsupported, workspace_id=workspace_id)
    with pytest.raises(WorkspaceFileReadError) as declared_error:
        reader.read(declared_oversize, workspace_id=workspace_id)
    with pytest.raises(WorkspaceFileReadError) as actual_error:
        reader.read(actual_oversize, workspace_id=workspace_id)

    assert unsupported_error.value.code == "workspace_file_content_type_unsupported"
    assert declared_error.value.code == "workspace_file_too_large"
    assert actual_error.value.code == "workspace_file_too_large"


def test_agent_file_reader_rejects_runtime_denied_and_secret_named_files(
    tmp_path: Path,
) -> None:
    workspace_id = uuid4()
    storage = LocalStorage(str(tmp_path / "storage"))
    reader = WorkspaceFileContentReader(storage)
    denied = _file(
        workspace_id,
        filename="notes.txt",
        content_type="text/plain",
        content=b"denied",
    )
    denied.runtime_access = "denied"
    secret_named = _file(
        workspace_id,
        filename=".env.production",
        content_type="text/plain",
        content=b"secret",
    )

    with pytest.raises(WorkspaceFileReadError) as denied_error:
        reader.read(denied, workspace_id=workspace_id)
    with pytest.raises(WorkspaceFileReadError) as secret_error:
        reader.read(secret_named, workspace_id=workspace_id)

    assert denied_error.value.code == "project_input_runtime_access_denied"
    assert secret_error.value.code == "project_input_sensitive_file_denied"


def _file(
    workspace_id: UUID,
    *,
    filename: str,
    content_type: str,
    content: bytes,
) -> WorkspaceFile:
    return WorkspaceFile(
        workspace_id=workspace_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        checksum_sha256=sha256(content).hexdigest(),
        storage_key=f"workspaces/{workspace_id}/files/{filename}",
        status="active",
        sensitivity="internal",
        runtime_access="allowed",
    )
