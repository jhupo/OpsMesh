"""Resume publication of verified assets without replacing previously uploaded bytes."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from opsmesh_operator.contracts import ReleaseManifest, require_tag

from scripts.release import file_record


def command(*args: str) -> str:
    return subprocess.check_output(list(args), text=True).strip()


def verify_assets(assets: list[dict[str, object]], directory: Path) -> set[str]:
    local = {path.name: file_record(path) for path in directory.iterdir() if path.is_file()}
    present: set[str] = set()
    for asset in assets:
        name = str(asset["name"])
        record = local.get(name)
        if (
            record is None
            or name in present
            or asset.get("state") != "uploaded"
            or asset.get("size") != record.size
            or asset.get("digest") != f"sha256:{record.sha256}"
        ):
            raise ValueError(f"Existing release asset does not match candidate: {name}")
        present.add(name)
    return present


def release_notes(manifest: ReleaseManifest) -> str:
    archives = sorted(file.name for file in manifest.files if file.name.startswith(
        ("opsmesh-cli-", "opsmesh-server-")
    ))
    lines = [
        "## Ready-to-run distributions", "",
        "Download and extract the archive for your platform. Python/pip/uv are not required.",
        "Keep the executable with its accompanying runtime directory; do not copy only the binary.",
        "", *[f"- `{name}`" for name in archives], "",
        "CLI: `./opsmesh version` (Windows: `opsmesh.exe version`).",
        "Server (Linux amd64): `./opsmesh-server check`, then `migrate`, `api` or `worker`.",
        "PostgreSQL, Redis and deployment host services remain external prerequisites.", "",
        "## Integrity and provenance", "",
        "`checksums.txt` lists SHA-256 hashes. Verify the downloaded archive before execution:",
        "```sh",
        f"gh attestation verify ARCHIVE --repo {manifest.repository} "
        f"--signer-workflow {manifest.repository}/.github/workflows/release-publish.yml "
        f"--source-ref refs/tags/{manifest.tag} --source-digest {manifest.commit} "
        "--deny-self-hosted-runners", "```", "",
        "## Container images", "", f"- `{manifest.image('backend')}`",
        f"- `{manifest.image('runtime')}`", "",
        "The `.whl` and source distributions are developer packages, not standalone executables.",
        "",
    ]
    return "\n".join(lines)


def publish(tag: str, repository: str, directory: Path) -> None:
    require_tag(tag)
    manifest = ReleaseManifest.model_validate_json(
        (directory / "release-manifest.json").read_text("utf-8")
    )
    if manifest.tag != tag or manifest.repository != repository:
        raise ValueError("Release identity mismatch")
    if manifest.commit != command("git", "rev-parse", "HEAD"):
        raise ValueError("Release commit mismatch")
    expected = {record.name for record in manifest.files} | {"release-manifest.json"}
    actual = {path.name for path in directory.iterdir() if path.is_file()}
    if actual != expected:
        raise ValueError("Release inventory mismatch")
    for record in manifest.files:
        if file_record(directory / record.name) != record:
            raise ValueError(f"Candidate checksum mismatch: {record.name}")
    pages = json.loads(command(
        "gh", "api", f"repos/{repository}/releases?per_page=100", "--paginate", "--slurp"
    ))
    existing = [release for page in pages for release in page if release["tag_name"] == tag]
    if not existing:
        # Require an existing remote tag; creating a release must never create one implicitly.
        command("gh", "api", f"repos/{repository}/git/ref/tags/{tag}")
        # The creation response owns the new ID. List results can lag behind a successful POST.
        existing = [json.loads(command(
            "gh", "api", f"repos/{repository}/releases", "--method", "POST",
            "--raw-field", f"tag_name={tag}", "--field", "draft=true",
            "--raw-field", f"body={release_notes(manifest)}",
            "--field", "generate_release_notes=true",
            "--field", f"prerelease={'true' if 'rc' in tag else 'false'}",
        ))]
    if len(existing) != 1:
        raise ValueError("Expected exactly one release for the tag")
    release = existing[0]
    if release["tag_name"] != tag:
        raise ValueError("Release response tag mismatch")
    endpoint = f"repos/{repository}/releases/{release['id']}/assets?per_page=100"
    assets = [asset for page in json.loads(command(
        "gh", "api", endpoint, "--paginate", "--slurp"
    )) for asset in page]
    present = verify_assets(assets, directory)
    if not release["draft"]:
        if present != expected:
            raise ValueError("Published release is incomplete; refusing mutation")
        return
    for name in sorted(expected - present):
        command("gh", "release", "upload", tag, str(directory / name), "--repo", repository)
    assets = [asset for page in json.loads(command(
        "gh", "api", endpoint, "--paginate", "--slurp"
    )) for asset in page]
    if verify_assets(assets, directory) != expected:
        raise ValueError("Release upload verification failed")
    # A partial retry must match the persisted manifest before touching version image tags.
    for kind in ("backend", "runtime"):
        source = manifest.image(kind)
        target = f"{source.split('@')[0]}:{tag}"
        command("docker", "buildx", "imagetools", "create", "--tag", target, source)
    command("gh", "release", "edit", tag, "--repo", repository, "--draft=false")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    args = parser.parse_args()
    publish(args.tag, args.repository, args.directory)


if __name__ == "__main__":
    main()
