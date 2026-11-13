"""Exercise explicit inventory migration without Linux publication syscalls."""

import json

import pytest

from tools import migrate_plugin_paths as migration


def test_move_deepest_independent_plugin_before_its_parent(
    tmp_path, monkeypatch
):
    parent = tmp_path / "data/demo/worker"
    child = parent / "child"
    child.mkdir(parents=True)
    (child / "retained.txt").write_text("child")
    (parent / "parent.txt").write_text("parent")
    unknown = tmp_path / "data/unknown"
    unknown.mkdir()
    calls = []

    def move(source, destination):
        calls.append(source)
        source.rename(destination)

    monkeypatch.setattr(migration, "_publish_new_directory", move)
    modules = ("demo.worker", "demo.worker.child")
    migration.migrate(tmp_path, modules)
    assert calls == [child, parent]
    assert (
        tmp_path / "data/demo.worker.child/retained.txt"
    ).read_text() == "child"
    assert (tmp_path / "data/demo.worker/parent.txt").read_text() == "parent"
    assert migration.unknown_directories(tmp_path, modules) == [str(unknown)]
    migration.migrate(tmp_path, modules)
    assert calls == [child, parent]


def test_conflict_aborts_all_moves_before_mutation(tmp_path, monkeypatch):
    (tmp_path / "data/demo/one").mkdir(parents=True)
    (tmp_path / "data/demo/two").mkdir()
    (tmp_path / "data/demo.two").mkdir()
    moved = []
    monkeypatch.setattr(
        migration, "_publish_new_directory", lambda *args: moved.append(args)
    )
    with pytest.raises(ValueError, match="Conflicting"):
        migration.migrate(tmp_path, ("demo.one", "demo.two"))
    assert not moved
    assert not (tmp_path / migration.JOURNAL).exists()


def test_resume_identifies_a_move_completed_before_journal_commit(
    tmp_path, monkeypatch
):
    source = tmp_path / "data/demo/worker"
    source.mkdir(parents=True)
    target = tmp_path / "data/demo.worker"
    identity = migration._identity(source)
    source.rename(target)
    journal = {
        "version": 1,
        "plugins": ["demo.worker"],
        "moves": {str(source): {"identity": identity, "completed": False}},
    }
    (tmp_path / migration.JOURNAL).write_text(json.dumps(journal))
    moved = []
    monkeypatch.setattr(
        migration, "_publish_new_directory", lambda *args: moved.append(args)
    )
    migration.migrate(tmp_path, ("demo.worker",))
    assert target.is_dir()
    assert not moved


def test_inventory_rejects_symlinked_legacy_root(tmp_path):
    base = tmp_path / "data"
    outside = tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    try:
        (base / "demo").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are unavailable")
    with pytest.raises(ValueError, match="Symbolic links"):
        migration.inventory(tmp_path, ("demo.worker",))


@pytest.mark.parametrize(
    "plugins", [["demo", "demo"], ["../escape"], ["demo..worker"]]
)
def test_manifest_rejects_ambiguous_or_unsafe_names(tmp_path, plugins):
    manifest = tmp_path / "inventory.json"
    manifest.write_text(json.dumps({"plugins": plugins}))
    with pytest.raises(ValueError):
        migration.read_plugins(manifest)
