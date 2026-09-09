"""Build native CLI and relocatable server archives from the locked release wheels."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.12.14"
CLI_PLATFORMS = ("linux-amd64", "linux-arm64", "windows-amd64", "darwin-amd64", "darwin-arm64")


def host_platform() -> str:
    machine = {"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
    return f"{platform.system().lower()}-{machine[platform.machine()]}"


def archive_tree(directory: Path, output: Path) -> None:
    """Emit regular files only; dereference contained library links at build time."""
    paths = sorted(path for path in directory.rglob("*") if path.is_file())
    if len(paths) > 50_000 or sum(path.stat().st_size for path in paths) > 1_000_000_000:
        raise ValueError("Distribution exceeds the operator's archive extraction limits")
    for path in paths:
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Distribution contains a link outside its build root")
    if output.suffix == ".zip":
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in paths:
                archive.write(path, path.relative_to(directory).as_posix())
    else:
        with tarfile.open(output, "w:gz", dereference=True) as archive:
            for path in paths:
                archive.add(path, arcname=path.relative_to(directory).as_posix(), recursive=False)


def copy_server_assets(target: Path) -> None:
    for name in ("README.md", "alembic.ini"):
        shutil.copy2(ROOT / name, target / name)
    shutil.copytree(
        ROOT / "backend/migrations",
        target / "backend/migrations",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(ROOT / "deploy/server", target / "deploy/server")
    shutil.copy2(ROOT / ".env.example", target / ".env.example")
    shutil.copy2(ROOT / "docs/standalone-distributions.md", target / "DISTRIBUTION.md")
    shutil.copy2(ROOT / "deploy/server/opsmesh-server", target / "opsmesh-server")
    (target / "opsmesh-server").chmod(0o755)


def build(kind: str, tag: str, wheels: Path, output: Path, uv: str) -> Path:
    if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+(?:rc[0-9]+)?", tag) is None:
        raise ValueError("Expected a canonical release tag")
    for project in (ROOT, ROOT / "operator", ROOT / "runtime"):
        version = tomllib.loads((project / "pyproject.toml").read_text("utf-8"))["project"][
            "version"
        ]
        if version != tag.removeprefix("v"):
            raise ValueError("Native artifact tag does not match checkout version")
    target_platform = host_platform()
    if target_platform not in CLI_PLATFORMS:
        raise ValueError("Unsupported native build platform")
    if kind == "server" and target_platform != "linux-amd64":
        raise ValueError("Server runtime currently supports Linux amd64 only")
    package_version = tag.removeprefix("v")
    wheels = wheels.resolve()
    output.mkdir(parents=True, exist_ok=True)
    suffix = "zip" if target_platform.startswith("windows-") else "tar.gz"
    archive = output.resolve() / f"opsmesh-{kind}-{tag}-{target_platform}.{suffix}"
    if archive.exists():
        raise ValueError("Refusing to overwrite a built artifact")
    with tempfile.TemporaryDirectory(prefix="opsmesh-native-build-") as temporary:
        work = Path(temporary)

        def run(*args: str, **kwargs: object) -> None:
            subprocess.run(list(args), check=True, cwd=ROOT, **kwargs)

        requirements = work / "requirements.txt"
        selection = ["--package", "opsmesh-operator", "--extra", "host"] if kind == "cli" else []
        run(
            uv,
            "export",
            "--quiet",
            "--frozen",
            "--no-dev",
            "--no-emit-workspace",
            "--no-header",
            "--no-annotate",
            *selection,
            "--output-file",
            str(requirements),
        )
        if kind == "cli":
            environment = work / "environment"
            run(uv, "venv", "--python", PYTHON_VERSION, str(environment))
            python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            build_requirements = work / "build-requirements.txt"
            run(
                uv,
                "export",
                "--quiet",
                "--frozen",
                "--only-group",
                "packaging",
                "--no-emit-workspace",
                "--output-file",
                str(build_requirements),
            )
            run(
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--require-hashes",
                "--no-deps",
                "-r",
                str(requirements),
                "-r",
                str(build_requirements),
            )
            run(
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--no-deps",
                str(wheels / f"opsmesh_operator-{package_version}-py3-none-any.whl"),
            )
            run(
                str(python),
                "-m",
                "PyInstaller",
                "--onedir",
                "--name",
                "opsmesh",
                "--distpath",
                str(work / "frozen"),
                "--workpath",
                str(work / "analysis"),
                "--specpath",
                str(work),
                "--recursive-copy-metadata",
                "opsmesh-operator",
                "--collect-all",
                "docker",
                "--collect-all",
                "dotenv",
                "--collect-all",
                "psycopg",
                "--collect-all",
                "psycopg_binary",
                str(ROOT / "scripts/cli_entry.py"),
            )
            bundle = work / "frozen/opsmesh"
            shutil.copy2(ROOT / "docs/standalone-distributions.md", bundle / "DISTRIBUTION.md")
        else:
            installs = work / "managed-python"
            run(uv, "python", "install", PYTHON_VERSION, "--install-dir", str(installs), "--no-bin")
            # uv also creates minor-version aliases; use its discovery API, not directory globbing.
            installed_python = subprocess.check_output(
                [uv, "python", "find", "--managed-python", "--no-project", PYTHON_VERSION],
                env={**os.environ, "UV_PYTHON_INSTALL_DIR": str(installs)},
                text=True,
            ).strip()
            python_home = Path(installed_python).resolve().parent.parent
            bundle = work / "server"
            bundle.mkdir()
            shutil.copytree(python_home, bundle / "python", symlinks=False)
            python = bundle / "python/bin/python3"
            run(
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--break-system-packages",
                "--require-hashes",
                "--no-deps",
                "--only-binary",
                ":all:",
                "--link-mode",
                "copy",
                "-r",
                str(requirements),
            )
            run(
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--break-system-packages",
                "--no-deps",
                "--link-mode",
                "copy",
                str(wheels / f"opsmesh-{package_version}-py3-none-any.whl"),
                str(wheels / f"opsmesh_operator-{package_version}-py3-none-any.whl"),
            )
            copy_server_assets(bundle)
            shutil.copy2(requirements, bundle / "requirements.lock.txt")
        # Preserve the installed distribution licenses alongside the native executable.
        site = Path(
            subprocess.check_output(
                [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
                text=True,
            ).strip()
        )
        for metadata in site.glob("*.dist-info"):
            shutil.copytree(metadata, bundle / "THIRD_PARTY" / metadata.name)
        (bundle / "BUILD.json").write_text(
            json.dumps(
                {
                    "tag": tag,
                    "platform": target_platform,
                    "kind": kind,
                    "python": PYTHON_VERSION,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        archive_tree(bundle, archive)
    return archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["cli", "server"], required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--wheels", type=Path, default=ROOT / "dist")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    print(build(args.kind, args.tag, args.wheels, args.output, args.uv), flush=True)


if __name__ == "__main__":
    main()
