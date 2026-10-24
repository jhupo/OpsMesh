"""Build a Linux executable bundle on its target architecture, without running it."""

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
from importlib.metadata import distributions, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Require a release tag matching pyproject.toml")
    arguments = parser.parse_args()
    release_version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    if arguments.tag and arguments.tag != f"v{release_version}":
        parser.error("Tag must equal v + the pyproject.toml version")
    architecture = platform.machine()
    if platform.system() != "Linux" or architecture not in {"x86_64", "aarch64"}:
        parser.error("Build on Linux x86_64 or aarch64 for that same architecture")
    output = ROOT / "dist"
    bundle = output / "opsmesh"
    if output.exists() and any(output.iterdir()):
        parser.error("dist must be empty; preserve existing artifacts before building")
    output.mkdir(exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--name",
        "opsmesh",
        "--distpath",
        str(output / "frozen"),
        "--workpath",
        str(output / "work"),
        "--specpath",
        str(output / "spec"),
        "--copy-metadata",
        "opsmesh",
        "--recursive-copy-metadata",
        "opsmesh-sdk",
        "--recursive-copy-metadata",
        "openai-agents",
        "--collect-all",
        "opsmesh",
        "--collect-all",
        "opsmesh_sdk",
        "--collect-all",
        "agents",
        "--collect-submodules",
        "mcp.client",
        "--collect-submodules",
        "mcp.server",
        "--collect-submodules",
        "mcp.shared",
        "--collect-all",
        "nonebot",
        "--collect-all",
        "nonebot.adapters.qq",
        "--recursive-copy-metadata",
        "nonebot2",
        "--recursive-copy-metadata",
        "nonebot-adapter-qq",
        "--collect-all",
        "dingtalk_stream",
        "--collect-all",
        "docker",
        "--collect-all",
        "psycopg_binary",
        "--collect-submodules",
        "uvicorn",
        "--collect-submodules",
        "sqlalchemy.dialects.postgresql",
        "--add-data",
        f"{ROOT / 'src/opsmesh/infra/migrations'}:opsmesh/infra/migrations",
        str(ROOT / "scripts/server_entry.py"),
    ]
    subprocess.run(command, check=True, cwd=ROOT)
    bundle.mkdir()
    shutil.move(output / "frozen/opsmesh", bundle / "bin")
    shutil.copytree(ROOT / "deploy/systemd", bundle / "systemd")
    shutil.copyfile(ROOT / "LICENSE", bundle / "LICENSE")
    dependency_licenses = bundle / "licenses"
    dependency_licenses.mkdir()
    inventory = []
    for distribution in distributions():
        name = distribution.metadata["Name"]
        if not name:
            continue
        included = []
        for path in distribution.files or []:
            if path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE", "AUTHORS")):
                original = Path(distribution.locate_file(path))
                if original.is_file():
                    safe_parts = [part for part in path.parts if part not in {".", "..", "/", "\\"}]
                    destination = dependency_licenses / name / Path(*safe_parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original, destination)
                    included.append(destination.relative_to(bundle).as_posix())
        inventory.append({"name": name, "version": distribution.version, "license_files": included})
    (bundle / "dependencies.json").write_text(json.dumps(inventory, indent=2) + "\n")
    for name in ("install.sh", "opsmesh.env.example", "README.md"):
        (bundle / name).write_text((ROOT / "deploy" / name).read_text(), newline="\n")
    (bundle / "install.sh").chmod(0o755)
    (bundle / "release.json").write_text(
        json.dumps(
            {
                "format": 1,
                "version": release_version,
                "platform": f"linux-{architecture}",
                "python": platform.python_version(),
                "agents_sdk": version("openai-agents"),
                "license": "MIT",
                "source": f"https://github.com/jhupo/OpsMesh/releases/tag/v{release_version}",
            },
            indent=2,
        )
        + "\n"
    )
    archive_path = output / f"opsmesh-v{release_version}-linux-{architecture}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(bundle, arcname="opsmesh")
    with archive_path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (output / "SHA256SUMS").write_text(f"{digest}  {archive_path.name}\n")
    print(archive_path)


if __name__ == "__main__":
    main()
