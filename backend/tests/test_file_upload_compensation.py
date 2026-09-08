from pathlib import Path

import pytest

from backend.app.api.services.files import WorkspaceFileService
from backend.app.files.models import WorkspaceFile
from backend.app.files.storage import LocalStorage
from backend.tests.test_product_tools import _seed_workspace, _session


def test_failed_upload_commit_preserves_previous_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _session()
    user, workspace = _seed_workspace(session)
    storage = LocalStorage(str(tmp_path))
    service = WorkspaceFileService(session, storage, 1024)
    kwargs = dict(workspace_id=workspace.id, uploaded_by_user_id=user.id,
                  filename="test.txt", content_type="text/plain", content=b"same content")
    original = service.upload_file(**kwargs)

    def fail() -> None:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(session, "commit", fail)
    with pytest.raises(RuntimeError, match="commit failed"):
        service.upload_file(**kwargs)
    assert storage.read(original.storage_key) == b"same content"
    assert session.query(WorkspaceFile).count() == 1
    assert len([path for path in tmp_path.rglob("*") if path.is_file()]) == 1
