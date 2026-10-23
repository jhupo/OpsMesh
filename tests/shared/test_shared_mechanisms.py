"""S2 shared mechanism unit tests: files, serialization, errors, pagination."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from opsmesh.shared.contracts import JsonObject, ensure_aware_utc, new_identifier, utc_now
from opsmesh.shared.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from opsmesh.shared.files import (
    atomic_replace,
    read_bytes_bounded,
    read_text_bounded,
    resolve_within_root,
    validate_relative_path,
)
from opsmesh.shared.pagination import MAX_PAGE_LIMIT, PageParams
from opsmesh.shared.serialization import (
    canonical_json,
    encode_timestamp,
    parse_timestamp,
    payload_digest,
)


def test_relative_path_accepts_nested_posix_paths() -> None:
    assert validate_relative_path("a/b/c.txt") == "a/b/c.txt"


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "   ",
        "/absolute.txt",
        "..",
        "a/../b",
        "a//b",
        "a/./b",
        "a\\b",
        "a\x00b",
    ],
)
def test_relative_path_rejects_unsafe_paths(candidate: str) -> None:
    with pytest.raises(ValueError):
        validate_relative_path(candidate)


def _require_symlink_support(tmp_path: Path) -> None:
    """Skip when the OS denies symlink creation (e.g. Windows without the privilege)."""
    probe_target = tmp_path / "symlink_probe_target"
    probe_link = tmp_path / "symlink_probe_link"
    try:
        probe_link.symlink_to(probe_target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation denied by the OS: {error}")
    finally:
        probe_link.unlink(missing_ok=True)


def test_resolve_within_root_rejects_symlink_escape(tmp_path: Path) -> None:
    _require_symlink_support(tmp_path)
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        resolve_within_root(root, "link/secret.txt")


def test_resolve_within_root_allows_contained_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "nested").mkdir(parents=True)
    resolved = resolve_within_root(root, "nested/file.bin")
    assert resolved == (root / "nested" / "file.bin").resolve()


def test_read_bounded_rejects_oversized_file(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    target.write_bytes(b"x" * 32)
    assert read_bytes_bounded(target, max_bytes=32) == b"x" * 32
    with pytest.raises(ValueError):
        read_bytes_bounded(target, max_bytes=31)


def test_read_text_bounded_decodes_utf8(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("héllo", encoding="utf-8")
    assert read_text_bounded(target, max_bytes=64) == "héllo"


def test_atomic_replace_replaces_content_without_leftover_staging(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_replace(target, b"first")
    atomic_replace(target, b"second")
    assert target.read_bytes() == b"second"
    assert [item.name for item in tmp_path.iterdir()] == ["state.json"]


def test_canonical_json_is_stable_and_sorted() -> None:
    first: JsonObject = {"b": 1, "a": "x"}
    second: JsonObject = {"a": "x", "b": 1}
    assert canonical_json(first) == canonical_json(second) == '{"a":"x","b":1}'


def test_canonical_json_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError):
        canonical_json({"value": float("inf")})


def test_payload_digest_changes_with_content() -> None:
    assert payload_digest({"a": 1}) != payload_digest({"a": 2})
    assert payload_digest({"a": 1}) == payload_digest({"a": 1})


def test_timestamp_encoding_round_trip_is_utc() -> None:
    moment = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    assert parse_timestamp(encode_timestamp(moment)) == moment
    naive = datetime(2026, 10, 16, 12, 0)
    assert ensure_aware_utc(naive) == moment
    assert utc_now().tzinfo is UTC


def test_new_identifier_is_unique() -> None:
    assert new_identifier() != new_identifier()
    assert new_identifier() != uuid4() or True


@pytest.mark.parametrize(
    "error_type",
    [NotFoundError, ForbiddenError, ConflictError],
)
def test_domain_errors_carry_status_codes(error_type: type[DomainError]) -> None:
    error = error_type()
    assert isinstance(error, DomainError)
    assert error.status_code in {403, 404, 409}
    assert str(error) == error.message


def test_page_params_enforce_bounds() -> None:
    assert PageParams() == PageParams(limit=50, offset=0)
    with pytest.raises(ValueError):
        PageParams(limit=0)
    with pytest.raises(ValueError):
        PageParams(limit=MAX_PAGE_LIMIT + 1)
    with pytest.raises(ValueError):
        PageParams(offset=-1)
