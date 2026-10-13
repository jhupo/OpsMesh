from opsmesh_operator.contracts import ReleaseFile, ReleaseManifest


def manifest(tag: str = "v0.1.0") -> ReleaseManifest:
    return ReleaseManifest(
        tag=tag,
        repository="jhupo/OpsMesh",
        commit="a" * 40,
        api_image="ghcr.io/jhupo/opsmesh-api@sha256:" + "b" * 64,
        worker_image="ghcr.io/jhupo/opsmesh-worker@sha256:" + "d" * 64,
        runtime_image="ghcr.io/jhupo/opsmesh-runtime@sha256:" + "c" * 64,
        database_revision="0071_platform_delivery",
        upgrade_from_revisions=["0071_platform_delivery"],
        rollback_database_revisions=[],
        platforms=["linux/amd64"],
        files=[ReleaseFile(name="bundle.tar.gz", size=1, sha256="d" * 64)],
    )
