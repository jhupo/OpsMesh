"""One packaging implementation for local validation and GitHub releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
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


def validate_release_policy(policy: dict[str, object], revision: str, config: Config) -> None:
    """Require the release to admit the immediately previous schema revision."""
    script = ScriptDirectory.from_config(config).get_revision(revision)
    if script is None:
        raise ValueError(f"Current database revision is not present: {revision}")
    raw_down_revision = script.down_revision
    if raw_down_revision is None:
        return
    down_revisions = (
        {raw_down_revision}
        if isinstance(raw_down_revision, str)
        else set(raw_down_revision)
    )
    supported = policy.get("upgrade_from_revisions")
    if not isinstance(supported, list) or not down_revisions.issubset(set(supported)):
        missing = sorted(down_revisions - set(supported or []))
        raise ValueError(
            "release-policy.json must include immediate predecessor revisions: "
            + ", ".join(missing)
        )


def file_record(path: Path) -> ReleaseFile:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return ReleaseFile(name=path.name, size=path.stat().st_size, sha256=digest)


def write_checksums(output: Path) -> None:
    records = [
        file_record(path)
        for path in sorted(output.iterdir())
        if path.is_file() and path.name not in {"checksums.txt", "release-manifest.json"}
    ]
    (output / "checksums.txt").write_text(
        "".join(f"{record.sha256}  {record.name}\n" for record in records),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate", "manifest"])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--repository", default="jhupo/OpsMesh")
    parser.add_argument("--backend-digest")
    parser.add_argument("--runtime-digest")
    args = parser.parse_args()
    validate_version(args.tag)
    policy = json.loads((ROOT / "release-policy.json").read_text("utf-8"))
    config = Config(str(ROOT / "alembic.ini"))
    revision = ScriptDirectory.from_config(config).get_current_head()
    validate_release_policy(policy, revision, config)
    if args.command == "validate":
        return
    args.output.mkdir(parents=True, exist_ok=True)
    from scripts.build_standalone import CLI_PLATFORMS

    required = {"install.sh", f"opsmesh-server-{args.tag}-linux-amd64.tar.gz"} | {
        f"opsmesh-cli-{args.tag}-{target}.{'zip' if target.startswith('windows-') else 'tar.gz'}"
        for target in CLI_PLATFORMS
    }
    if not required <= {path.name for path in args.output.iterdir()}:
        raise ValueError("Release is missing required standalone distributions")
    write_checksums(args.output)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    files = [
        file_record(path)
        for path in sorted(args.output.iterdir())
        if path.is_file() and path.name != "release-manifest.json"
    ]
    manifest = ReleaseManifest(
        tag=args.tag,
        commit=commit,
        repository=args.repository,
        backend_digest=args.backend_digest,
        runtime_digest=args.runtime_digest,
        database_revision=revision,
        files=files,
        **policy,
    )
    (args.output / "release-manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
