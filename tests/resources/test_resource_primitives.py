"""S5 resource primitive tests: content storage and authorization contracts."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from opsmesh.resources.files.access import (
    RejectingScopeAdmission,
    ResourceReader,
    ScopeRef,
    validate_permissions,
)
from opsmesh.resources.files.content import content_download_response
from opsmesh.resources.storage.filesystem import (
    ContentStorageError,
    content_storage_key,
    delete_content,
    new_content_id,
    read_content,
    write_content,
)
from opsmesh.shared.errors import ForbiddenError, InvalidArgumentError


def test_write_read_and_delete_roundtrip(tmp_path: Path) -> None:
    content_id = new_content_id()
    digest = write_content(tmp_path, content_id=content_id, payload=b"hello")
    assert read_content(tmp_path, content_id=content_id, expected_digest=digest) == b"hello"
    delete_content(tmp_path, content_id=content_id)
    assert not (tmp_path / content_storage_key(content_id)).exists()


def test_read_refuses_content_that_changed_its_digest(tmp_path: Path) -> None:
    content_id = new_content_id()
    write_content(tmp_path, content_id=content_id, payload=b"hello")
    with pytest.raises(ContentStorageError):
        read_content(tmp_path, content_id=content_id, expected_digest="0" * 64)


def test_write_refuses_oversized_content(tmp_path: Path) -> None:
    from opsmesh.resources.storage.filesystem import MAX_CONTENT_BYTES

    content_id = new_content_id()
    with pytest.raises(ContentStorageError):
        write_content(tmp_path, content_id=content_id, payload=b"\0" * (MAX_CONTENT_BYTES + 1))


def test_permissions_are_validated() -> None:
    assert validate_permissions(["read", "write"]) == ["read", "write"]
    with pytest.raises(InvalidArgumentError):
        validate_permissions(["read", "read"])
    with pytest.raises(InvalidArgumentError):
        validate_permissions(["own"])


def test_the_default_scope_admission_refuses_every_external_scope() -> None:
    admission = RejectingScopeAdmission()
    reader = ResourceReader(user_id=uuid4())
    admission.admit(reader=reader, scope=ScopeRef())
    assert ScopeRef().is_user_scope
    with pytest.raises(ForbiddenError):
        admission.admit(reader=reader, scope=ScopeRef(workspace_id=uuid4()))
    with pytest.raises(ForbiddenError):
        admission.admit(reader=reader, scope=ScopeRef(project_id=uuid4()))


def test_download_response_sanitizes_the_filename() -> None:
    response = content_download_response(
        b"data", filename='evil"\r\nX-Evil: 1', media_type=None
    )
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition == 'attachment; filename="evilX-Evil: 1"'
    assert response.media_type == "application/octet-stream"
