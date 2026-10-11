from opsmesh.runtime.instances.policies.safety import is_digest_pinned_image


def test_local_image_identity_is_immutable_without_a_registry() -> None:
    assert is_digest_pinned_image("sha256:" + "a" * 64)
    assert is_digest_pinned_image("ghcr.io/jhupo/opsmesh-runtime@sha256:" + "a" * 64)
    assert not is_digest_pinned_image("localhost:5001/opsmesh-runtime:latest")
    assert not is_digest_pinned_image("sha256:abc")
