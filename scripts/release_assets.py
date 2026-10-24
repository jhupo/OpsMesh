"""Create explicit, reviewable release sources, installers, checksums and notes."""

import argparse
import hashlib
import json
import re
import shutil
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = (
    "src",
    "sdk/python/src",
    "sdk/python/pyproject.toml",
    "sdk/python/README.md",
    "sdk/python/LICENSE",
    "sdk/python/AGENTS.md",
    "sdk/python/CONTRIBUTING.md",
    "sdk/python/SECURITY.md",
    "sdk/python/CHANGELOG.md",
    "sdk/python/.gitattributes",
    "sdk/python/.gitignore",
    "sdk/python/.github",
    "sdk/python/examples",
    "examples",
    "deploy",
    "scripts/build_release.py",
    "scripts/audit_requirements.py",
    "scripts/release_assets.py",
    "scripts/server_entry.py",
    ".github",
    ".agents",
    ".env.example",
    ".dockerignore",
    ".gitignore",
    ".gitattributes",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "AGENTS.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "docs",
    "tests",
)


def source_files() -> list[Path]:
    files: set[Path] = set()
    for name in SOURCE_PATHS:
        path = ROOT / name
        if not path.exists():
            raise ValueError(f"Missing release source: {name}")
        for candidate in path.rglob("*") if path.is_dir() else [path]:
            if candidate.is_symlink():
                raise ValueError(f"Source links are not permitted: {candidate}")
            if candidate.is_file() and not any(
                part in {"__pycache__", ".venv", "dist", "build"} or part.endswith("_cache")
                for part in candidate.relative_to(ROOT).parts
            ):
                files.add(candidate)
    return sorted(files)


def sources(output: Path, version: str) -> None:
    prefix = f"opsmesh-v{version}"
    files = source_files()
    if any((output / f"{prefix}-source.{suffix}").exists() for suffix in ("tar.gz", "zip")):
        raise ValueError("Source archives already exist; preserve them before rebuilding")
    with tarfile.open(output / f"{prefix}-source.tar.gz", "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=f"{prefix}/{path.relative_to(ROOT).as_posix()}")
    with zipfile.ZipFile(output / f"{prefix}-source.zip", "x", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, f"{prefix}/{path.relative_to(ROOT).as_posix()}")
    (output / "install.sh").write_text((ROOT / "deploy/setup.sh").read_text(), newline="\n")
    shutil.copyfile(ROOT / "LICENSE", output / "LICENSE")


def notes(output: Path, version: str, repository: str) -> None:
    binaries = [f"opsmesh-v{version}-linux-{arch}.tar.gz" for arch in ("x86_64", "aarch64")]
    required = binaries + [
        f"opsmesh-v{version}-source.tar.gz",
        f"opsmesh-v{version}-source.zip",
        f"opsmesh-{version}-py3-none-any.whl",
        f"opsmesh-{version}.tar.gz",
        f"opsmesh_sdk-{version}-py3-none-any.whl",
        f"opsmesh_sdk-{version}.tar.gz",
        "install.sh",
        "LICENSE",
        "IMAGE_DIGEST.txt",
        "linux-x86_64.cdx.json",
        "linux-aarch64.cdx.json",
        "source.cdx.json",
        "security-linux-x86_64.json",
        "security-linux-aarch64.json",
        "security-image-amd64.json",
        "security-image-arm64.json",
        "source-security.json",
    ]
    missing = [name for name in required if not (output / name).is_file()]
    if missing:
        raise ValueError(f"Release assets are incomplete: {missing}")
    image = (output / "IMAGE_DIGEST.txt").read_text().strip()
    if not re.fullmatch(r"ghcr\.io/[a-z0-9_./-]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("Missing immutable image digest")
    metadata = {
        "version": version,
        "license": "MIT",
        "source": f"https://github.com/{repository}/tree/v{version}",
        "image": image,
        "platforms": ["linux/amd64", "linux/arm64"],
        "sdk": {"name": "opsmesh-sdk", "version": version, "python": ">=3.11.8"},
        "binary_assets": binaries,
    }
    (output / "release-manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    assets = sorted(
        p
        for p in output.iterdir()
        if p.is_file() and p.name not in {"SHA256SUMS", "RELEASE_NOTES.md"}
    )
    lines = []
    for path in assets:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        lines.append(f"{digest}  {path.name}\n")
    (output / "SHA256SUMS").write_text("".join(lines))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    marker = f"## [{version}]"
    if marker not in changelog:
        raise ValueError("Add release notes to CHANGELOG.md before tagging")
    changes = changelog.split(marker, 1)[1].split("\n## ", 1)[0].strip()
    changes = changes.split("\n", 1)[-1].strip() if changes.startswith(" - ") else changes
    body = f"""# OpsMesh v{version}

{changes}

## Installation assets

- Linux x86_64 and aarch64 executable bundles include systemd units and installation scripts. Ubuntu 22.04 / glibc 2.35 or newer is required.
- `install.sh` downloads a pinned binary release and verifies its SHA-256 checksum before installation.
- Explicit `source.tar.gz` and `source.zip` archives contain the matching host, SDK, dependencies lock, deployment files and build scripts.
- `opsmesh_sdk-*.whl` and `opsmesh_sdk-*.tar.gz` are the standalone, typed plugin SDK for Python 3.11.8+.
- Host Python distributions are also attached. Install the matching SDK wheel before installing the host wheel.
- `SHA256SUMS`, CycloneDX SBOMs, vulnerability reports and `release-manifest.json` describe the assets. GitHub artifact attestations bind builds to their workflow.

```sh
sha256sum --ignore-missing -c SHA256SUMS
gh attestation verify opsmesh-v{version}-linux-x86_64.tar.gz --repo {repository}
sudo sh install.sh v{version} --config /root/opsmesh-install.env --start
pip install ./opsmesh_sdk-{version}-py3-none-any.whl
```

## Container image

```sh
docker pull {image}
```

Published tags: `ghcr.io/{repository.lower()}:{version}`, `:{version.rsplit(".", 1)[0]}`, and `:latest`. Both linux/amd64 and linux/arm64 are included. Compose deployment is documented in the source archive's `deploy/README.md`.

## Upgrade and compatibility

Back up PostgreSQL, the data volume and the encryption key. Drain queued/running/waiting tasks before upgrading; checkpoints require the same Agents SDK version. Run the included migrations before starting workers. Database downgrades are not automatic. The plugin SDK contains development contracts and a packaging CLI, not a business API client.

## Verification and license

Publication requires static checks, isolated PostgreSQL regressions, dependency and secret scanning, binary/container vulnerability scans, and CodeQL. CI does not invoke models or external messaging platforms. Successful publication is not a platform acceptance result. OpsMesh and its plugin SDK are licensed MIT; bundled dependencies retain their own license notices.
"""
    (output / "RELEASE_NOTES.md").write_text(body, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sources", "notes", "check-version"))
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    parser.add_argument("--tag")
    parser.add_argument("--repository", default="jhupo/OpsMesh")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    sdk_version = tomllib.loads((ROOT / "sdk/python/pyproject.toml").read_text())["project"][
        "version"
    ]
    if (
        not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
        or version != sdk_version
        or (args.tag and args.tag != f"v{version}")
    ):
        parser.error("Host version, SDK version and release tag must match")
    if args.command == "check-version":
        return
    args.output.mkdir(parents=True, exist_ok=True)
    if args.command == "sources":
        sources(args.output, version)
    else:
        notes(args.output, version, args.repository)


if __name__ == "__main__":
    main()
