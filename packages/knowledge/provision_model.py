"""Explicit offline resource preparation, separate from plugin startup."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
MODEL_REVISION = "7999e1d3359715c523056ef9478215996d62a620"


def prepare(destination: Path, revision: str = MODEL_REVISION) -> Path:
    """Download a pinned model and write a complete checksum manifest."""
    from huggingface_hub import snapshot_download

    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Model destination must be new or empty")
    destination.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MODEL_NAME,
        revision=revision,
        local_dir=str(destination),
        ignore_patterns=["onnx/*", "openvino/*", "*.h5", "*.msgpack"],
    )
    files = {}
    for path in destination.rglob("*"):
        if path.is_file():
            files[path.relative_to(destination).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    manifest = destination / "opsmesh-model-manifest.json"
    manifest.write_text(
        json.dumps(
            {"model": MODEL_NAME, "revision": revision, "files": files},
            sort_keys=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    """Prepare model resources independently from framework startup."""
    parser = argparse.ArgumentParser(
        description="Explicitly provision the pinned local BGE model"
    )
    parser.add_argument("destination", type=Path)
    parser.add_argument("--revision", default=MODEL_REVISION)
    arguments = parser.parse_args()
    print(prepare(arguments.destination, arguments.revision))


if __name__ == "__main__":
    main()
