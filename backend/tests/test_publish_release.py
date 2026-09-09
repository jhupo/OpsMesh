import json
from pathlib import Path

import pytest
from opsmesh_operator.contracts import ReleaseManifest

from scripts import check_release, publish_release
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


@pytest.mark.parametrize("draft,new_release", [(True, False), (False, False), (True, True)])
def test_publication_reuses_existing_assets_and_published_release_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, draft: bool, new_release: bool
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
            if "/git/ref/" in args[2]:
                return "{}"
            if "POST" in args:
                assert "draft=true" in args
                assert "generate_release_notes=true" in args
                assert "prerelease=true" in args
                return json.dumps({"tag_name": manifest.tag, "id": 1, "draft": True})
            if "/assets?" in args[2]:
                return json.dumps([assets])
            if new_release:
                # Simulate a release list that remains stale after creation.
                return "[[]]"
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
    if new_release:
        assert len([call for call in calls if "--slurp" in call and "/assets?" not in call[2]]) == 1
        tag_check = ("gh", "api", f"repos/{manifest.repository}/git/ref/tags/{manifest.tag}")
        creation = next(call for call in calls if "POST" in call)
        assert calls.index(tag_check) < calls.index(creation)


@pytest.mark.parametrize("tampered", [False, True])
def test_public_download_verification_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tampered: bool
) -> None:
    package = tmp_path / "package.whl"
    package.write_bytes(b"candidate")
    manifest = ReleaseManifest(
        tag="v0.1.0rc4", commit="b" * 40, repository="jhupo/OpsMesh",
        backend_digest="sha256:" + "c" * 64, runtime_digest="sha256:" + "d" * 64,
        database_revision="0072_release_schema", upgrade_from_revisions=[],
        rollback_database_revisions=[], connector_protocol=2, platforms=["linux/amd64"],
        files=[file_record(package)],
    )
    calls: list[list[str]] = []
    verified: list[tuple[str, str | None]] = []
    monkeypatch.setattr(check_release.ReleaseSource, "fetch_manifest", lambda *args: manifest)
    monkeypatch.setattr(check_release.ReleaseSource, "download_file", lambda *args: package)

    def verify(self: object, path: Path, tag: str, *, commit: str | None = None) -> None:
        assert path == package
        if tampered:
            raise ValueError("Invalid provenance")
        verified.append((tag, commit))

    monkeypatch.setattr(check_release.ReleaseSource, "verify", verify)
    monkeypatch.setattr(check_release, "run_command", lambda args, **kwargs: calls.append(args))
    if tampered:
        with pytest.raises(ValueError, match="Invalid provenance"):
            check_release.verify_release(manifest.tag, manifest.repository)
        assert not calls
    else:
        check_release.verify_release(manifest.tag, manifest.repository)
        assert verified == [(manifest.tag, manifest.commit)]
        assert len(calls) == 2
        for kind, call in zip(("backend", "runtime"), calls, strict=True):
            assert call[3] == f"oci://{manifest.image(kind)}"
            assert call[call.index("--source-digest") + 1] == manifest.commit
            assert call[call.index("--source-ref") + 1] == f"refs/tags/{manifest.tag}"
            assert "--deny-self-hosted-runners" in call
