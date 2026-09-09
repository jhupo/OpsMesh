import tarfile
from pathlib import Path

import pytest
import yaml
from alembic.config import Config
from alembic.script import ScriptDirectory
from opsmesh_operator.contracts import ReleaseFile, ReleaseManifest, require_tag
from pydantic import ValidationError

from scripts.release import build_bundle, file_record, validate_version

ROOT = Path(__file__).resolve().parents[2]


def test_migration_chain_fits_alembic_version_column() -> None:
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert len(scripts.get_heads()) == 1
    for revision in scripts.walk_revisions():
        assert len(revision.revision) <= 32, revision.revision


def test_release_versions_are_aligned() -> None:
    validate_version("v0.1.0")
    with pytest.raises(ValueError, match="does not match"):
        validate_version("v9.9.9")


@pytest.mark.parametrize("tag", ["../v1.0.0", "1.0.0", "v1.0", "v1.0.0;id", "v1.0.0+local"])
def test_release_tag_cannot_be_a_path_or_command(tag: str) -> None:
    with pytest.raises(ValueError):
        require_tag(tag)


def test_bundle_contains_migration_config_without_local_secrets(tmp_path: Path) -> None:
    bundle = build_bundle(tmp_path, "v0.1.0")
    with tarfile.open(bundle) as archive:
        names = archive.getnames()
    assert "alembic.ini" in names
    assert "backend/migrations/env.py" in names
    assert "operator/pyproject.toml" in names
    assert not any("__pycache__" in name or name.endswith(".env") for name in names)
    assert file_record(bundle).size > 0


def test_manifest_pins_images_and_rejects_duplicate_files() -> None:
    file = ReleaseFile(name="bundle.tar.gz", sha256="a" * 64, size=10)
    manifest = ReleaseManifest(
        tag="v0.1.0", commit="b" * 40, repository="jhupo/OpsMesh",
        backend_digest="sha256:" + "c" * 64, runtime_digest="sha256:" + "d" * 64,
        database_revision="0070_memory_lifecycle", upgrade_from_revisions=[],
        rollback_database_revisions=[], connector_protocol=2, platforms=["linux/amd64"],
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
    assert "head_sha=$GITHUB_SHA&status=success" in publish
    assert "packages: write" in publish
    assert "subject-path: dist/*" in publish
    assert "--draft=false" in publish
    ci = (ROOT / ".github/workflows/backend-ci.yml").read_text("utf-8")
    assert "branches: [master]" in ci
    assert "uv run pytest\n" not in ci


def test_images_use_locked_dependencies_and_explicit_migrations() -> None:
    for name in ("Dockerfile", "Dockerfile.runtime"):
        dockerfile = (ROOT / name).read_text("utf-8")
        assert "uv sync --frozen --no-dev --no-editable" in dockerfile
        assert "pip install ." not in dockerfile
        assert "USER opsmesh" in dockerfile
        assert "uv.lock" in dockerfile
    assert "alembic upgrade" not in (ROOT / "Dockerfile").read_text("utf-8")
