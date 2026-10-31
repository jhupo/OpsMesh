"""Pinned Git imports read objects without checkout hooks, filters or submodule execution."""

import subprocess

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.capabilities.api import GitSkillSource
from opsmesh.modules.capabilities.internal.git_skills import GitSkillImporter
from opsmesh.modules.capabilities.internal.skills import PersistentSkillCatalog
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService


def git(directory, *arguments, data=None):
    return (
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *arguments],
            cwd=directory,
            input=data,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .strip()
    )


def repository(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init")
    skill = path / "skills" / "test"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\ndescription: A pinned skill\n---\nInstructions", encoding="utf-8"
    )
    (skill / "run.py").write_text("raise RuntimeError('must not execute')", encoding="utf-8")
    (path / ".gitattributes").write_text("* filter=malicious\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-m", "initial")
    return path, git(path, "rev-parse", "HEAD")


async def test_import_fixed_commit_subdirectory_publish_load_and_replay(tmp_path):
    repo, commit = repository(tmp_path)
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "objects")
    skills = PersistentSkillCatalog(
        database, workspaces, git_importer=GitSkillImporter(local_roots=(tmp_path,))
    )
    await database.initialize()
    source = GitSkillSource(repository=str(repo), commit=commit, subdirectory="skills/test")
    try:
        first = await skills.publish_git("example", "1", source)
        assert first.source_commit == commit and first.description == "A pinned skill"
        assert {item.path for item in first.files} == {"SKILL.md", "run.py"}
        assert await skills.publish_git("example", "1", source) == first
        (repo / "skills/test/SKILL.md").write_text("changed", encoding="utf-8")
        git(repo, "add", ".")
        git(repo, "commit", "-m", "changed")
        assert await skills.publish_git("example", "1", source) == first
        await workspaces.create("destination")
        await skills.load(first.ref, "destination")
        assert b"A pinned skill" in await workspaces.read("destination", ".agents/example/SKILL.md")
    finally:
        await database.close()


async def test_import_refuses_untrusted_repository_symlink_and_oversize(tmp_path):
    repo, commit = repository(tmp_path)
    source = GitSkillSource(repository=str(repo), commit=commit, subdirectory="skills/test")
    with pytest.raises(OpsMeshError) as untrusted:
        async with GitSkillImporter().import_source(source):
            pytest.fail("Local repository bypassed allowlist")
    assert untrusted.value.code == "AUTHORIZATION_DENIED"
    importer = GitSkillImporter(local_roots=(tmp_path,), max_bytes=100)
    with pytest.raises(OpsMeshError) as oversized:
        async with importer.import_source(source):
            pytest.fail("Oversized import succeeded")
    assert oversized.value.code == "RESOURCE_LIMIT"
    # Build a symlink tree entry directly; this also works without Windows symlink privilege.
    digest = git(repo, "hash-object", "-w", "--stdin", data=b"../../../secret")
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{digest},skills/test/link")
    git(repo, "commit", "-m", "unsafe symlink")
    unsafe = source.model_copy(update={"commit": git(repo, "rev-parse", "HEAD")})
    with pytest.raises(OpsMeshError) as symlink:
        async with GitSkillImporter(local_roots=(tmp_path,)).import_source(unsafe):
            pytest.fail("Symlink import succeeded")
    assert symlink.value.code == "PATH_INVALID"


@pytest.mark.parametrize("directory", ["../secret", "/absolute", "a/../../b", ".git", "a\\b"])
async def test_import_rejects_unsafe_subdirectory_before_fetch(tmp_path, directory):
    repo, commit = repository(tmp_path)
    source = GitSkillSource(repository=str(repo), commit=commit, subdirectory=directory)
    with pytest.raises(OpsMeshError) as failure:
        async with GitSkillImporter(local_roots=(tmp_path,)).import_source(source):
            pytest.fail("Unsafe directory accepted")
    assert failure.value.code == "PATH_INVALID"
