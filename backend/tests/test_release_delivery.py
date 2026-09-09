import ast
import tarfile
import tomllib
from pathlib import Path

import pytest
import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory
from opsmesh_operator.contracts import ReleaseFile, ReleaseManifest, require_tag
from pydantic import ValidationError

from scripts.build_standalone import archive_tree, copy_server_assets
from scripts.release import file_record, validate_version, write_checksums

ROOT = Path(__file__).resolve().parents[2]


def test_migration_chain_fits_alembic_version_column() -> None:
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert len(scripts.get_heads()) == 1
    for revision in scripts.walk_revisions():
        assert len(revision.revision) <= 32, revision.revision


def test_long_explicit_constraint_names_use_alembic_naming() -> None:
    for path in (ROOT / "backend/migrations/versions").glob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if node.value.startswith(("fk_", "ck_", "uq_", "pk_", "ix_")) and len(node.value) > 63:
                assert any(
                    isinstance(parent, ast.Call)
                    and isinstance(parent.func, ast.Attribute)
                    and parent.func.attr == "f"
                    and node in parent.args
                    for parent in ast.walk(tree)
                ), f"{path.name}:{node.lineno}: use op.f for {node.value}"


def test_release_versions_are_aligned() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    validate_version(f"v{project['project']['version']}")
    with pytest.raises(ValueError, match="does not match"):
        validate_version("v9.9.9")


@pytest.mark.parametrize("tag", ["../v1.0.0", "1.0.0", "v1.0", "v1.0.0;id", "v1.0.0+local"])
def test_release_tag_cannot_be_a_path_or_command(tag: str) -> None:
    with pytest.raises(ValueError):
        require_tag(tag)


def test_bundle_contains_migration_config_without_local_secrets(tmp_path: Path) -> None:
    staging = tmp_path / "server"
    staging.mkdir()
    copy_server_assets(staging)
    bundle = tmp_path / "server.tar.gz"
    archive_tree(staging, bundle)
    with tarfile.open(bundle) as archive:
        names = archive.getnames()
    assert "alembic.ini" in names
    assert "backend/migrations/env.py" in names
    assert "opsmesh-server" in names
    assert "operator/pyproject.toml" not in names
    assert not any("__pycache__" in name or name.endswith(".env") for name in names)
    assert file_record(bundle).size > 0


def test_manifest_pins_images_and_rejects_duplicate_files() -> None:
    file = ReleaseFile(name="bundle.tar.gz", sha256="a" * 64, size=10)
    manifest = ReleaseManifest(
        tag="v0.1.0",
        commit="b" * 40,
        repository="jhupo/OpsMesh",
        backend_digest="sha256:" + "c" * 64,
        runtime_digest="sha256:" + "d" * 64,
        database_revision="0070_memory_lifecycle",
        upgrade_from_revisions=[],
        rollback_database_revisions=[],
        connector_protocol=2,
        platforms=["linux/amd64"],
        files=[file],
    )
    assert manifest.image("backend") == "ghcr.io/jhupo/opsmesh@sha256:" + "c" * 64
    with pytest.raises(ValidationError, match="unique"):
        ReleaseManifest.model_validate({**manifest.model_dump(), "files": [file, file]})


def test_workflow_actions_are_pinned_and_publish_requires_gate() -> None:
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = yaml.safe_load(path.read_text("utf-8"))
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if "uses" in step:
                    assert len(step["uses"].split("@")[1]) == 40
    publish = (ROOT / ".github/workflows/release-publish.yml").read_text("utf-8")
    workflow = yaml.load(publish, Loader=yaml.BaseLoader)
    assert workflow["on"] == {"push": {"tags": ["v*.*.*"]}}
    assert workflow["jobs"]["gate"]["uses"] == "./.github/workflows/release-prepare.yml"
    assert workflow["jobs"]["gate"]["with"]["tag"] == "${{ github.ref_name }}"
    assert workflow["jobs"]["gate"]["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["candidate"]["needs"] == "gate"
    assert workflow["jobs"]["publish"]["needs"] == ["gate", "candidate", "standalone"]
    assert workflow["jobs"]["standalone"]["needs"] == "gate"
    assert "if" not in workflow["jobs"]["publish"]
    assert "workflow_dispatch" not in publish
    gate = yaml.load(
        (ROOT / ".github/workflows/release-prepare.yml").read_text("utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert set(gate["on"]) == {"workflow_call"}
    assert gate["permissions"] == {"contents": "read"}
    steps = gate["jobs"]["gate"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert 'test "$GITHUB_REF" = "refs/tags/$RELEASE_TAG"' in commands
    assert "git merge-base --is-ancestor HEAD origin/master" in commands
    for required in (
        "uv run pytest",
        "uv run ruff check .",
        "uv run mypy",
        "uv run alembic upgrade head",
        "uv run alembic check",
        "uv build --all-packages",
    ):
        assert required in commands
    assert not any("continue-on-error" in step for step in steps)
    assert "packages: write" in publish
    assert "subject-path: dist/*" in publish
    assert "scripts.publish_release" in publish
    assert "mcp_stdio_client --check" in publish
    assert "uv build" not in publish
    assert "docker build" not in commands
    assert "candidate-${{ github.run_id }}-${{ github.run_attempt }}" in publish
    ci = (ROOT / ".github/workflows/backend-ci.yml").read_text("utf-8")
    assert "branches: [master]" in ci
    assert "uv run pytest\n" not in ci


def test_checksums_cover_artifacts_not_their_own_digest(tmp_path: Path) -> None:
    path = tmp_path / "server.tar.gz"
    path.write_bytes(b"runtime")
    write_checksums(tmp_path)
    expected = f"{file_record(path).sha256}  server.tar.gz\n"
    assert (tmp_path / "checksums.txt").read_text() == expected
    write_checksums(tmp_path)
    assert (tmp_path / "checksums.txt").read_text() == expected


def test_images_use_locked_dependencies_and_explicit_migrations() -> None:
    for name in ("Dockerfile", "Dockerfile.runtime"):
        dockerfile = (ROOT / name).read_text("utf-8")
        assert "uv sync --frozen --no-dev --no-editable" in dockerfile
        assert "pip install ." not in dockerfile
        assert "USER opsmesh" in dockerfile
        assert "uv.lock" in dockerfile
    assert "alembic upgrade" not in (ROOT / "Dockerfile").read_text("utf-8")
