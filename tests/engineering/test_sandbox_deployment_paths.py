"""The daemon must see the same persistent path that native sandbox Workers authorize."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("deployment", ["local", "server"])
def test_compose_storage_path_is_identical_in_api_worker_and_daemon(deployment):
    compose = yaml.safe_load((ROOT / f"deploy/{deployment}/compose.yml").read_text())
    for name in ("api", "worker"):
        service = compose["services"][name]
        storage = service["environment"]["OPSMESH_STORAGE_ROOT"]
        mounts = [
            mount
            for mount in service["volumes"]
            if isinstance(mount, dict) and mount.get("target") == storage
        ]
        assert len(mounts) == 1
        assert mounts[0]["type"] == "bind"
        assert mounts[0]["source"] == storage
    if deployment == "local":
        assert compose["services"]["storage-init"]["user"] == "0:0"
        assert compose["services"]["api"]["depends_on"]["storage-init"] == {
            "condition": "service_completed_successfully"
        }
