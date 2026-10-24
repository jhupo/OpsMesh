"""Audit the metadata-only QQ fork under its unchanged upstream name/version."""

import argparse
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
QQ_COMMIT = "e79e4a0830b55107752e706b2807d6651d29d463"
QQ_REFERENCE = f"git+https://github.com/jhupo/OpsMesh.git@{QQ_COMMIT}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exported", type=Path, help="uv export --no-hashes output")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    packages = tomllib.loads((ROOT / "uv.lock").read_text())["package"]
    qq = next(package for package in packages if package["name"] == "nonebot-adapter-qq")
    if qq["version"] != "1.7.3" or urlsplit(qq["source"]["git"]).fragment != QQ_COMMIT:
        raise ValueError("Review the QQ source diff before updating its audit identity")
    exported = args.exported.read_text()
    requirement = f"nonebot-adapter-qq @ {QQ_REFERENCE}"
    if exported.splitlines().count(requirement) != 1 or "--hash=" in exported:
        raise ValueError("Expected the reviewed source pin in a hash-free uv export")
    # Vulnerability databases identify this unmodified Python code by PyPI name/version.
    # uv sync verifies the actual Git source pin; no dependency or advisory is skipped.
    exported = exported.replace(requirement, "nonebot-adapter-qq==1.7.3")
    args.output.write_text(f"# QQ source provenance: {QQ_REFERENCE}\n" + exported, encoding="utf-8")


if __name__ == "__main__":
    main()
