import pytest
from opsmesh_operator.releases import ReleaseDownloader
from opsmesh_operator.updates import activate_operator


@pytest.mark.parametrize("draft,pre", [(True, False), (False, True)])
def test_latest_discovery_rejects_unpublished_or_prerelease(monkeypatch, draft, pre):
    monkeypatch.setattr(
        ReleaseDownloader,
        "_get_json",
        lambda *a, **k: {
            "tag_name": "v0.2.0",
            "draft": draft,
            "prerelease": pre,
        },
    )
    with pytest.raises(ValueError, match="stable"):
        ReleaseDownloader("jhupo/OpsMesh").latest_tag()


def test_latest_discovery_uses_published_stable(monkeypatch):
    monkeypatch.setattr(
        ReleaseDownloader,
        "_get_json",
        lambda *a, **k: {
            "tag_name": "v0.2.0",
            "draft": False,
            "prerelease": False,
        },
    )
    assert ReleaseDownloader("jhupo/OpsMesh").latest_tag() == "v0.2.0"


@pytest.mark.skipif(__import__("os").name != "posix", reason="Host activation requires POSIX")
def test_operator_activation_switches_symlink_without_overwriting_running_files(tmp_path):
    old = tmp_path / "operator-releases/old"
    new = tmp_path / "operator-releases/new"
    old.mkdir(parents=True)
    new.mkdir()
    (tmp_path / "operator").symlink_to(old, target_is_directory=True)
    activate_operator(tmp_path, new)
    assert (tmp_path / "operator").resolve() == new
    assert old.is_dir()
