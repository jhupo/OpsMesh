"""Verify publicly downloaded release bytes and provenance after publication."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from opsmesh_operator.commands import run_command
from opsmesh_operator.releases import ReleaseSource


def verify_attestation(path: str, tag: str, commit: str, repository: str) -> None:
    run_command([
        "gh", "attestation", "verify", path, "--repo", repository,
        "--signer-workflow", f"{repository}/.github/workflows/release-publish.yml",
        "--source-ref", f"refs/tags/{tag}", "--source-digest", commit,
        "--deny-self-hosted-runners",
    ], timeout=120)


def verify_release(tag: str, repository: str) -> None:
    source = ReleaseSource(repository)
    with tempfile.TemporaryDirectory(prefix="opsmesh-release-verification-") as temporary:
        directory = Path(temporary)
        manifest = source.fetch_manifest(tag, directory)
        for record in manifest.files:
            path = source.download_file(manifest, record, directory)
            verify_attestation(str(path), tag, manifest.commit, repository)
            print(f"Verified download, checksum and provenance: {record.name}", flush=True)
        for kind in ("backend", "runtime"):
            image = manifest.image(kind)
            verify_attestation(f"oci://{image}", manifest.tag, manifest.commit, repository)
            print(f"Verified image provenance: {image}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", required=True)
    args = parser.parse_args()
    verify_release(args.tag, args.repository)


if __name__ == "__main__":
    main()
