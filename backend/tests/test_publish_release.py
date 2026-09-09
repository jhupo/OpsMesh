import json
from pathlib import Path

import pytest
from opsmesh_operator.contracts import ReleaseManifest

from scripts import publish_release
from scripts.publish_release import verify_assets
from scripts.release import file_record


def test_existing_assets_are_verified_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "package.whl"
    path.write_bytes(b"verified candidate")
    record = file_record(path)
    asset = {
        "name": path.name, "state": "uploaded", "size": record.size,
        "digest": f"sha256:{record.sha256}",
    }
    assert verify_assets([asset], tmp_path) == {path.name}
    assert verify_assets([], tmp_path) == set()
    for field, value in (
        ("name", "unexpected.whl"), ("state", "starter"), ("size", 0),
        ("digest", "sha256:" + "0" * 64), ("digest", None),
    ):
        with pytest.raises(ValueError, match="does not match"):
            verify_assets([{**asset, field: value}], tmp_path)
    with pytest.raises(ValueError, match="does not match"):
        verify_assets([asset, asset], tmp_path)


@pytest.mark.parametrize("draft", [True, False])
def test_publication_reuses_existing_assets_and_published_release_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, draft: bool
) -> None:
    package = tmp_path / "package.whl"
    package.write_bytes(b"candidate")
    manifest = ReleaseManifest(
        tag="v0.1.0rc2", commit="b" * 40, repository="jhupo/OpsMesh",
        backend_digest="sha256:" + "c" * 64, runtime_digest="sha256:" + "d" * 64,
        database_revision="0071_platform_delivery", upgrade_from_revisions=[],
        rollback_database_revisions=[], connector_protocol=2, platforms=["linux/amd64"],
        files=[file_record(package)],
    )
    (tmp_path / "release-manifest.json").write_text(manifest.model_dump_json())
    assets = [
        {"name": path.name, "size": path.stat().st_size, "state": "uploaded",
         "digest": "sha256:" + file_record(path).sha256}
        for path in tmp_path.iterdir()
    ]
    calls: list[tuple[str, ...]] = []

    def command(*args: str) -> str:
        calls.append(args)
        if args[:2] == ("git", "rev-parse"):
            return manifest.commit
        if args[:2] == ("gh", "api"):
            if "/assets?" in args[2]:
                return json.dumps([assets])
            return json.dumps([[{"tag_name": manifest.tag, "id": 1, "draft": draft}]])
        return ""

    monkeypatch.setattr(publish_release, "command", command)
    publish_release.publish(manifest.tag, manifest.repository, tmp_path)
    assert not any(call[:3] == ("gh", "release", "upload") for call in calls)
    mutations = [call for call in calls if call[0] == "docker" or call[:2] == ("gh", "release")]
    assert len(mutations) == (3 if draft else 0)
    if draft:
        assert mutations[-1][-1] == "--draft=false"
        assert mutations[0][-1] == manifest.image("backend")
