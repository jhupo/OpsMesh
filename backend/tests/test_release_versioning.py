import pytest
from packaging.version import Version

from backend.app.admin.releases.versioning import normalize_release_tag, release_version


def test_release_version_normalizes_supported_tags() -> None:
    assert normalize_release_tag(" 1.2.3 ") == "v1.2.3"
    assert normalize_release_tag("v1.2.3-rc.1+build.7") == "v1.2.3rc1+build.7"
    assert release_version("v2.0.0") == Version("2.0.0")


def test_release_version_orders_prerelease_before_stable() -> None:
    assert release_version("v1.2.3-rc.1") < release_version("v1.2.3")


@pytest.mark.parametrize(
    "tag",
    ["", "main", "1.2", "1.2.3.4", "1!1.2.3", "1.2.3.post1", "1.2.3.dev1"],
)
def test_release_version_rejects_non_release_tags(tag: str) -> None:
    with pytest.raises(ValueError, match="Release tag must be a version"):
        release_version(tag)
