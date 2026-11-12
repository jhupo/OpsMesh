"""Exercise atomic file operations and archive round trips."""

import io
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest

import opsmesh_filesystem as filesystem
from opsmesh_filesystem import _files, files
from opsmesh_filesystem._paths import Roots


@pytest.fixture
def space(tmp_path, monkeypatch):
    settings = filesystem.Settings(root=tmp_path / "files")
    roots = Roots.prepare(settings.root)
    value = roots.for_plugin("demo_plugin")
    monkeypatch.setattr(files, "_settings", settings)
    monkeypatch.setattr(files, "_roots", roots)
    monkeypatch.setattr(files, "_space", lambda: value)
    return value


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.anyio
async def test_write_and_read_round_trip_creates_parents(space):
    target = filesystem.data_file("notes/today.txt")
    written = await filesystem.write_file(target, "hello")
    assert written == target
    assert target.read_text(encoding="utf-8") == "hello"
    assert await filesystem.read_file(target) == b"hello"
    assert sorted(path.name for path in target.parent.iterdir()) == [
        "today.txt"
    ]


@pytest.mark.anyio
async def test_write_rejects_existing_file_without_replace(space):
    target = filesystem.data_file("state.bin")
    await filesystem.write_file(target, b"first")
    with pytest.raises(filesystem.PathRejected, match="already exists"):
        await filesystem.write_file(target, b"second", replace=False)
    assert target.read_bytes() == b"first"
    await filesystem.write_file(target, b"second")
    assert target.read_bytes() == b"second"


@pytest.mark.anyio
async def test_write_rejects_roots_and_foreign_paths(space, tmp_path):
    with pytest.raises(filesystem.PathRejected, match="root"):
        await filesystem.write_file(space.data_dir, b"nope")
    outside = tmp_path / "outside.txt"
    with pytest.raises(filesystem.PathRejected):
        await filesystem.write_file(outside, b"nope")
    assert not outside.exists()


@pytest.mark.anyio
async def test_read_missing_file_is_reported(space):
    with pytest.raises(filesystem.PathNotFound):
        await filesystem.read_file(filesystem.data_file("missing.txt"))


@pytest.mark.anyio
async def test_delete_removes_files_and_trees(space):
    file_path = filesystem.data_file("one.txt")
    await filesystem.write_file(file_path, b"one")
    await filesystem.delete(file_path)
    assert not file_path.exists()

    tree = filesystem.cache_file("tree")
    (tree / "nested").mkdir(parents=True)
    (tree / "nested" / "item.txt").write_text("x", encoding="utf-8")
    await filesystem.delete(tree)
    assert not tree.exists()

    with pytest.raises(filesystem.PathRejected, match="root"):
        await filesystem.delete(space.cache_dir)


@pytest.mark.anyio
async def test_publish_directory_creates_a_new_target(space):
    source = filesystem.data_file("source")
    (source / "nested").mkdir(parents=True)
    (source / "nested" / "file.txt").write_text("new", encoding="utf-8")
    destination = filesystem.data_file("published")

    result = await filesystem.publish_directory(source, destination)
    assert result == destination
    assert (destination / "nested" / "file.txt").read_text(
        encoding="utf-8"
    ) == "new"
    assert sorted(path.name for path in destination.parent.iterdir()) == [
        "published",
        "source",
    ]


@pytest.mark.anyio
async def test_publish_directory_preserves_existing_target(space):
    source = filesystem.data_file("source")
    source.mkdir(parents=True)
    (source / "file.txt").write_text("new", encoding="utf-8")
    destination = filesystem.data_file("published")
    destination.mkdir()
    original = destination / "file.txt"
    original.write_text("old", encoding="utf-8")

    with pytest.raises(filesystem.PathRejected, match="already exists"):
        await filesystem.publish_directory(source, destination)

    assert original.read_text(encoding="utf-8") == "old"


@pytest.mark.anyio
async def test_publish_directory_rejects_nested_destination(space):
    source = filesystem.data_file("source")
    source.mkdir(parents=True)

    with pytest.raises(filesystem.PathRejected, match="inside the source"):
        await filesystem.publish_directory(source, source / "nested")


@pytest.mark.anyio
async def test_foreign_plugin_operations_are_rejected(space):
    other = space.data_dir.parent / "other_plugin" / "file.txt"
    other.parent.mkdir(parents=True)
    other.write_text("other plugin", encoding="utf-8")

    for operation in (
        filesystem.read_file(other),
        filesystem.write_file(other, "overwritten"),
        filesystem.delete(other),
        filesystem.delete(space.data_dir.parent),
    ):
        with pytest.raises(filesystem.PathRejected, match="outside"):
            await operation

    assert other.read_text(encoding="utf-8") == "other plugin"


def test_concurrent_exclusive_file_writes_have_one_winner(tmp_path):
    target = tmp_path / "shared.txt"
    barrier = threading.Barrier(8)

    def write(index):
        barrier.wait(timeout=5)
        try:
            _files.write_bytes(target, str(index).encode(), replace=False)
            return index
        except filesystem.PathRejected:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = [
            value for value in pool.map(write, range(8)) if value is not None
        ]

    assert len(winners) == 1
    assert target.read_text() == str(winners[0])
    assert list(tmp_path.iterdir()) == [target]


def test_concurrent_publication_has_one_winner(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "published"
    settings = filesystem.Settings(root=tmp_path)
    barrier = threading.Barrier(8)

    def publish(index):
        barrier.wait(timeout=5)
        try:
            _files.publish_directory(source, destination, settings)
            return index
        except filesystem.PathRejected:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = [
            value for value in pool.map(publish, range(8)) if value is not None
        ]

    assert len(winners) == 1
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "published",
        "source",
    ]


def test_publication_rejects_target_created_during_copy(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "published"
    settings = filesystem.Settings(root=tmp_path)
    copy_tree = _files.archives.copy_tree

    def create_competing_directory(origin, staging, limits):
        copy_tree(origin, staging, limits)
        destination.mkdir()

    monkeypatch.setattr(
        _files.archives, "copy_tree", create_competing_directory
    )

    with pytest.raises(filesystem.PathRejected, match="already exists"):
        _files.publish_directory(source, destination, settings)

    assert destination.is_dir()
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "published",
        "source",
    ]


@pytest.mark.anyio
async def test_archive_extract_and_export_round_trip(space):
    archive = _zip(
        {
            "folder/": b"",
            "folder/file.txt": b"payload",
        }
    )
    destination = filesystem.data_file("imported")
    extracted = await filesystem.extract_archive(archive, destination)
    assert extracted == destination
    assert (destination / "folder" / "file.txt").read_bytes() == b"payload"

    exported = await filesystem.export_archive(destination)
    try:
        assert exported.path.is_file()
        with zipfile.ZipFile(exported.path) as snapshot:
            assert snapshot.read("folder/file.txt") == b"payload"
    finally:
        exported.discard()
    assert not exported.path.exists()


@pytest.mark.anyio
async def test_archive_extract_rejects_existing_and_unsafe_entries(space):
    destination = filesystem.data_file("existing")
    destination.mkdir(parents=True)
    with pytest.raises(filesystem.PathRejected, match="already exists"):
        await filesystem.extract_archive(_zip({"a.txt": b"a"}), destination)

    escaped = space.data_dir.parent / "escaped.txt"
    with pytest.raises(filesystem.ArchiveRejected):
        await filesystem.extract_archive(
            _zip({"../escaped.txt": b"bad"}),
            filesystem.data_file("unsafe"),
        )
    assert not escaped.exists()
    assert not filesystem.data_file("unsafe").exists()


@pytest.mark.anyio
async def test_archive_stream_must_be_binary(space):
    with pytest.raises(filesystem.ArchiveRejected):
        await filesystem.extract_archive(
            "not a stream", filesystem.data_file("bad")
        )


@pytest.mark.anyio
async def test_successful_extraction_removes_staged_upload(space):
    destination = filesystem.data_file("skill")
    await filesystem.extract_archive(
        _zip({"SKILL.md": b"content"}), destination
    )
    assert sorted(path.name for path in destination.parent.iterdir()) == [
        "skill"
    ]


@pytest.mark.anyio
async def test_export_rejects_source_containing_its_destination(space):
    space.cache_dir.mkdir(parents=True)
    (space.cache_dir / "cached.txt").write_text("cached", encoding="utf-8")

    with pytest.raises(filesystem.PathRejected, match="inside its source"):
        await filesystem.export_archive(space.cache_dir)

    assert sorted(path.name for path in space.cache_dir.iterdir()) == [
        "cached.txt"
    ]


@pytest.mark.anyio
async def test_export_rejects_cache_link_into_another_plugin(space):
    source = filesystem.data_file("source")
    source.mkdir(parents=True)
    (source / "file.txt").write_text("content", encoding="utf-8")
    space.cache_dir.mkdir(parents=True)
    other = space.cache_dir.parent / "other_plugin"
    other.mkdir()
    try:
        (space.cache_dir / ".exports").symlink_to(
            other, target_is_directory=True
        )
    except OSError:
        pytest.skip("Symbolic links are not available")

    with pytest.raises(filesystem.PathRejected, match="outside"):
        await filesystem.export_archive(source)

    assert list(other.iterdir()) == []
