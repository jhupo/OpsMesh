"""Docker image metadata does not replace the persisted environment authority."""

from types import SimpleNamespace

import pytest

from opsmesh.execution.runtime.manifest import trusted_manifest
from opsmesh.execution.runtime.sandbox import validate_container
from opsmesh.shared.errors import ConflictError


def container_and_state(path):
    labels = {"opsmesh.environment": "environment", "opsmesh.principal": "owner"}
    state = SimpleNamespace(container_id="native", labels=labels)
    container = SimpleNamespace(
        id="native",
        reload=lambda: None,
        attrs={
            "Config": {"Labels": {**labels, "org.opencontainers.image.revision": "build"}},
            "HostConfig": {"NetworkMode": "bridge", "Privileged": False},
            "Mounts": [
                {"Destination": "/workspace", "Type": "bind", "Source": str(path), "RW": True}
            ],
        },
    )
    return container, state


def test_image_labels_and_default_network_preserve_environment_identity(tmp_path):
    container, state = container_and_state(tmp_path)
    validate_container(container, state, tmp_path)
    assert trusted_manifest(tmp_path).root == "/tmp/opsmesh_runtime"


@pytest.mark.parametrize("change", ["owner", "container", "mount", "privileged"])
def test_modified_environment_authority_is_rejected(tmp_path, change):
    container, state = container_and_state(tmp_path)
    if change == "owner":
        container.attrs["Config"]["Labels"]["opsmesh.principal"] = "other"
    elif change == "container":
        container.id = "replacement"
    elif change == "mount":
        container.attrs["Mounts"][0]["Source"] = str(tmp_path / "other")
    else:
        container.attrs["HostConfig"]["Privileged"] = True
    with pytest.raises(ConflictError):
        validate_container(container, state, tmp_path)
