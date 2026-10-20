"""Template helpers read immutable data without executing package code."""

import hashlib
import io
import json
import zipfile

import pytest

from opsmesh_sdk.composition.templates import load_template
from opsmesh_sdk.errors import ValidationError


def package(resource: bytes, *, path: str = "flows/review.json") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(path, resource)
        archive.writestr("main.py", b"raise RuntimeError('never import a static template package')")
    return output.getvalue()


def test_load_template_checks_fixed_resource_digest() -> None:
    resource = json.dumps({"graph": {"nodes": []}}).encode()
    payload = package(resource)
    digest = hashlib.sha256(resource).hexdigest()
    assert load_template(payload, path="flows/review.json", digest=digest) == {
        "graph": {"nodes": []}
    }
    with pytest.raises(ValidationError):
        load_template(payload, path="flows/review.json", digest="0" * 64)


@pytest.mark.parametrize(
    "path", ["../review.json", "/review.json", "x/../review.json", "x\\a.json"]
)
def test_load_template_refuses_noncanonical_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        load_template(
            package(b"{}", path=path), path=path, digest=hashlib.sha256(b"{}").hexdigest()
        )


def test_load_template_refuses_duplicate_members_and_oversized_json() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("flow.json", b"{}")
        with pytest.warns(UserWarning):
            archive.writestr("flow.json", b"{}")
    with pytest.raises(ValidationError):
        load_template(output.getvalue(), path="flow.json", digest=hashlib.sha256(b"{}").hexdigest())
    resource = b" " * 1_048_577
    with pytest.raises(ValidationError):
        load_template(
            package(resource), path="flows/review.json", digest=hashlib.sha256(resource).hexdigest()
        )
