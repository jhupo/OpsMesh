"""S5 resource primitive tests: content storage and authorization contracts."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from opsmesh.resources.files.access import (
    RejectingScopeAdmission,
    ResourceReader,
    ScopeRef,
    validate_permissions,
)
from opsmesh.resources.files.content import content_download_response
from opsmesh.resources.files.schemas import RuntimePolicyRequest
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
    # The refusing path never touches the session; an unbound one is enough.
    admission.admit(Session(), reader=reader, scope=ScopeRef())
    assert ScopeRef().is_user_scope
    with pytest.raises(ForbiddenError):
        admission.admit(Session(), reader=reader, scope=ScopeRef(configuration_id=uuid4()))


def test_download_response_sanitizes_the_filename() -> None:
    response = content_download_response(b"data", filename='evil"\r\nX-Evil: 1', media_type=None)
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition == 'attachment; filename="evilX-Evil: 1"'
    assert response.media_type == "application/octet-stream"


class TestOutputDeclarationContract:
    def test_declarations_are_typed_objects(self) -> None:
        request = RuntimePolicyRequest.model_validate(
            {
                "relative_paths": ["docs/notes.txt"],
                "output_declarations": [{"path": "out/report.md", "media_type": "text/markdown"}],
            }
        )
        assert request.output_declarations[0].path == "out/report.md"
        assert request.output_declarations[0].description == ""

    def test_unknown_declaration_fields_are_refused(self) -> None:
        with pytest.raises(ValidationError):
            RuntimePolicyRequest.model_validate(
                {"output_declarations": [{"path": "out/report.md", "bogus": True}]}
            )

    def test_declaration_path_and_description_are_bounded(self) -> None:
        with pytest.raises(ValidationError):
            RuntimePolicyRequest.model_validate({"output_declarations": [{"path": ""}]})
        with pytest.raises(ValidationError):
            RuntimePolicyRequest.model_validate({"output_declarations": [{"path": "x" * 1001}]})
        with pytest.raises(ValidationError):
            RuntimePolicyRequest.model_validate(
                {"output_declarations": [{"path": "out.md", "description": "y" * 2001}]}
            )
