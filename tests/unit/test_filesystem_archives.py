"""Validate bounded ZIP handling and tree copies."""

import io
import os
import zipfile

import pytest

from opsmesh.plugins.filesystem import _archives as archives
from opsmesh.plugins.filesystem.config import Settings
from opsmesh.plugins.filesystem.errors import ArchiveRejected, PathRejected


def archive_bytes(files, *, symlink=False):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            if symlink:
                info.external_attr = 0o120777 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def named_archive_bytes(name, data):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("placeholder")
        info.filename = name
        archive.writestr(info, data)
    return buffer.getvalue()


def encrypted_archive_bytes(name, data):
    raw = bytearray(named_archive_bytes(name, data))
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        index = raw.find(signature)
        raw[index + offset] |= 0x1
    return bytes(raw)


def extract(settings, data, destination):
    archive = destination.parent / f"{destination.name}.zip"
    archive.write_bytes(data)
    try:
        archives.extract_archive(archive, destination, settings)
    finally:
        archive.unlink(missing_ok=True)


@pytest.fixture
def settings(tmp_path):
    return Settings(root=tmp_path / "files")


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "/absolute.txt",
        "C:/drive.txt",
        "C:relative.txt",
        "CON.txt",
        "trailing. ",
        "bad<name>.txt",
    ],
)
def test_archive_member_paths_are_rejected(settings, tmp_path, name) -> None:
    destination = tmp_path / "out"
    with pytest.raises(ArchiveRejected):
        extract(settings, named_archive_bytes(name, "content"), destination)
    assert not destination.exists()


def test_backslash_and_colliding_names_are_rejected(settings, tmp_path):
    with pytest.raises(ArchiveRejected):
        extract(
            settings,
            named_archive_bytes("..\\escape.txt", "content"),
            tmp_path / "backslash",
        )
    with pytest.raises(ArchiveRejected):
        extract(
            settings,
            archive_bytes({"Report.txt": "one", "report.txt": "two"}),
            tmp_path / "case",
        )
    with pytest.raises(ArchiveRejected):
        extract(
            settings,
            archive_bytes({"caf\u00e9.txt": "one", "cafe\u0301.txt": "two"}),
            tmp_path / "unicode",
        )


def test_symlinks_encryption_and_type_conflicts_are_rejected(
    settings, tmp_path
):
    with pytest.raises(ArchiveRejected):
        extract(
            settings,
            archive_bytes({"link": "target"}, symlink=True),
            tmp_path / "symlink",
        )
    with pytest.raises(ArchiveRejected):
        extract(
            settings,
            encrypted_archive_bytes("secret.txt", "data"),
            tmp_path / "encrypted",
        )
    with pytest.raises(ArchiveRejected):
        extract(
            settings,
            archive_bytes({"a": "file", "a/b": "nested"}),
            tmp_path / "conflict",
        )


def test_archive_limits_are_enforced(tmp_path):
    limited = Settings(
        root=tmp_path / "files",
        max_entries=3,
        max_file_bytes=10,
        max_extracted_bytes=15,
        max_depth=2,
        max_path_length=20,
    )
    cases = [
        {f"f{index}": "x" for index in range(4)},
        {"big": "x" * 11},
        {"a": "x" * 10, "b": "y" * 10},
        {"a/b/c": "x"},
        {"long-name-over-limit-x": "x"},
    ]
    for index, members in enumerate(cases):
        destination = tmp_path / f"limited-{index}"
        with pytest.raises(ArchiveRejected):
            extract(limited, archive_bytes(members), destination)
        assert not destination.exists()


@pytest.mark.parametrize("data", [b"not a zip", b"", archive_bytes({})])
def test_empty_and_invalid_archives_are_rejected(settings, tmp_path, data):
    with pytest.raises(ArchiveRejected):
        extract(settings, data, tmp_path / "invalid")


def test_copy_tree_rejects_links_and_copies_regular_files(settings, tmp_path):
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "file.txt").write_text("value", encoding="utf-8")
    destination = tmp_path / "copy"
    archives.copy_tree(source, destination, settings)
    assert (destination / "nested" / "file.txt").read_text(
        encoding="utf-8"
    ) == "value"

    link = source / "link"
    try:
        os.symlink(source / "nested" / "file.txt", link)
    except OSError:
        pytest.skip("Symbolic links are not available")
    with pytest.raises(PathRejected, match="symbolic link"):
        archives.copy_tree(source, tmp_path / "linked", settings)
    assert not (tmp_path / "linked").exists()


def test_write_archive_round_trips_through_extract(settings, tmp_path):
    source = tmp_path / "source"
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "file.txt").write_text("payload", encoding="utf-8")
    snapshot = tmp_path / "snapshot.zip"
    written = archives.write_archive(source, snapshot)
    assert written > 0

    destination = tmp_path / "restored"
    archives.extract_archive(snapshot, destination, settings)
    assert (destination / "nested" / "file.txt").read_text(
        encoding="utf-8"
    ) == "payload"
