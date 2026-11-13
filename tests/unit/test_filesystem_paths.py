"""Validate plugin spaces, path helpers, and containment rules."""

import os

import pytest

import opsmesh.plugins.filesystem as filesystem
from opsmesh.plugins.filesystem import files
from opsmesh.plugins.filesystem._paths import Roots, owned_path


@pytest.fixture
def space(tmp_path, monkeypatch):
    settings = filesystem.Settings(root=tmp_path / "files")
    roots = Roots.prepare(settings.root)
    value = roots.for_plugin("demo_plugin")
    monkeypatch.setattr(files, "_settings", settings)
    monkeypatch.setattr(files, "_roots", roots)
    monkeypatch.setattr(files, "_space", lambda: value)
    return value


def test_plugin_space_uses_separate_roots(space):
    assert space.data_dir.name == "demo_plugin"
    assert space.data_dir.parent.name == "data"
    assert space.cache_dir.parent.name == "cache"
    assert space.config_dir.parent.name == "config"
    assert not space.data_dir.exists()


def test_files_are_located_without_creating_directories(space):
    target = filesystem.data_file("nested/settings.json")
    assert target == space.data_dir / "nested" / "settings.json"
    assert not target.parent.exists()


@pytest.mark.parametrize(
    "name",
    [
        "",
        "../escape.txt",
        "/absolute.txt",
        "C:/drive.txt",
        "back\\slash.txt",
        "trailing. ",
        "CON",
        "a//b",
        "nul\x00byte",
    ],
)
def test_invalid_file_names_are_rejected(space, name):
    with pytest.raises(filesystem.PathRejected):
        filesystem.data_file(name)


def test_relative_paths_resolve_inside_the_data_directory(space, tmp_path):
    path = files._owned("notes/a.txt", must_exist=False)
    assert path == space.data_dir / "notes" / "a.txt"


def test_absolute_paths_outside_the_space_are_rejected(space, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(filesystem.PathRejected, match="outside"):
        files._owned(outside, must_exist=True)


def test_symlinks_cannot_escape_the_space(space, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    space.data_dir.mkdir(parents=True)
    link = space.data_dir / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are not available")
    with pytest.raises(filesystem.PathRejected, match="outside"):
        files._owned(link / "secret.txt", must_exist=True)


def test_absolute_paths_inside_the_space_are_accepted(space):
    target = space.cache_dir / "result.json"
    assert files._owned(target, must_exist=False) == (
        target.resolve(strict=False)
    )


@pytest.mark.parametrize("kind", ["data", "cache", "config"])
def test_other_plugin_paths_are_rejected(space, kind):
    root = getattr(space, f"{kind}_dir").parent
    with pytest.raises(filesystem.PathRejected, match="outside"):
        files._owned(root / "other_plugin" / "file.txt", must_exist=False)


def test_link_into_another_plugin_is_rejected(space):
    other = space.data_dir.parent / "other_plugin"
    other.mkdir(parents=True)
    (other / "file.txt").write_text("other", encoding="utf-8")
    space.data_dir.mkdir()
    link = space.data_dir / "link"
    try:
        link.symlink_to(other, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links are not available")

    with pytest.raises(filesystem.PathRejected, match="outside"):
        files._owned(link / "file.txt", must_exist=True)


def test_independent_child_plugins_have_flat_nonoverlapping_roots(tmp_path):
    roots = Roots.prepare(tmp_path)
    parent = roots.for_plugin("demo")
    child = roots.for_plugin("demo.worker")
    assert child.data_dir == roots.data / "demo.worker"
    assert child.data_dir.parent == parent.data_dir.parent
    settings = filesystem.Settings(root=tmp_path)
    with pytest.raises(filesystem.PathRejected, match="outside"):
        owned_path(
            parent,
            child.data_dir / "secret",
            settings=settings,
            must_exist=False,
        )
    with pytest.raises(filesystem.PathRejected, match="outside"):
        owned_path(
            child,
            parent.data_dir / "secret",
            settings=settings,
            must_exist=False,
        )
