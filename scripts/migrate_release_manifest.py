"""Explicit, offline conversion of a version-1 installed release record.

Stop the host updater and back up the installation before running this command.
The running installer/updater accepts only version 2; this is a data migration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from opsmesh_operator.contracts import ReleaseManifest
from opsmesh_operator.files import atomic_write


def convert(source: Path, destination: Path) -> ReleaseManifest:
    if source.resolve() == destination.resolve() or destination.exists():
        raise ValueError("Migration requires a new destination; preserve the source record")
    payload = json.loads(source.read_bytes())
    if not isinstance(payload, dict) or payload.pop("schema_version", None) != 1:
        raise ValueError("Expected an installed version-1 release record")
    owner = str(payload.get("repository", "")).split("/")[0].lower()
    backend = payload.pop("backend_digest")
    runtime = payload.pop("runtime_digest")
    payload.update(
        schema_version=2,
        api_image=f"ghcr.io/{owner}/opsmesh-backend@{backend}",
        worker_image=f"ghcr.io/{owner}/opsmesh-backend@{backend}",
        runtime_image=f"ghcr.io/{owner}/opsmesh-runtime@{runtime}",
    )
    manifest = ReleaseManifest.model_validate(payload)
    atomic_write(destination, manifest.model_dump_json(indent=2), mode=0o644)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    convert(args.source, args.destination)


if __name__ == "__main__":
    main()
