"""One packaging implementation for local validation and GitHub releases."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
import tomllib
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from opsmesh_operator.contracts import ReleaseFile, ReleaseManifest, require_tag

ROOT = Path(__file__).resolve().parents[1]


def validate_version(tag: str) -> None:
    require_tag(tag)
    for directory in (ROOT, ROOT / "runtime", ROOT / "operator"):
        project = tomllib.loads((directory / "pyproject.toml").read_text("utf-8"))
        if project["project"]["version"] != tag.removeprefix("v"):
            raise ValueError(f"Tag does not match {directory.name} package version")


def file_record(path: Path) -> ReleaseFile:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return ReleaseFile(name=path.name, size=path.stat().st_size, sha256=digest)


def build_bundle(output: Path, tag: str) -> Path:
    """Explicit allowlist: no checkout secrets, caches or generated local files."""
    bundle = output / f"opsmesh-server-{tag}.tar.gz"
    with tarfile.open(bundle, "w:gz") as archive:
        for name in ("README.md", "alembic.ini", "pyproject.toml", "uv.lock"):
            archive.add(ROOT / name, arcname=name)
        for directory in ("backend", "operator", "runtime", "deploy", "scripts"):
            for path in sorted((ROOT / directory).rglob("*")):
                relative = path.relative_to(ROOT)
                if any(part.startswith(".") or part in {"__pycache__", "build"}
                       or part.endswith(".egg-info") for part in relative.parts):
                    continue
                if path.is_file() and path.suffix in {".py", ".toml", ".yml", ".json", ".sh",
                                                      ".service", ".example"}:
                    archive.add(path, arcname=relative.as_posix())
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate", "bundle", "manifest"])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--repository", default="jhupo/OpsMesh")
    parser.add_argument("--backend-digest")
    parser.add_argument("--runtime-digest")
    args = parser.parse_args()
    validate_version(args.tag)
    if args.command == "validate":
        return
    args.output.mkdir(parents=True, exist_ok=True)
    if args.command == "bundle":
        build_bundle(args.output, args.tag)
        return
    policy = json.loads((ROOT / "release-policy.json").read_text("utf-8"))
    revision = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    files = [file_record(path) for path in sorted(args.output.iterdir())
             if path.is_file() and path.name != "release-manifest.json"]
    manifest = ReleaseManifest(
        tag=args.tag, commit=commit, repository=args.repository,
        backend_digest=args.backend_digest, runtime_digest=args.runtime_digest,
        database_revision=revision, files=files, **policy,
    )
    (args.output / "release-manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
