"""Audit exported dependencies for the official QQ botpy SDK."""

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "src/opsmesh/gateway/adapters/qq/SDK.json"


def qq_sdk() -> dict[str, str]:
    import json

    source = json.loads(SDK.read_text(encoding="utf-8"))
    if (
        source.get("format") != 1
        or source.get("name") != "qq-botpy"
        or source.get("version") != "1.2.1"
        or source.get("repository") != "https://github.com/tencent-connect/botpy"
    ):
        raise ValueError("Review the official QQ SDK identity before updating the audit source")
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exported", type=Path, help="uv export --no-hashes output")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source = qq_sdk()
    exported = args.exported.read_text(encoding="utf-8")
    lines = exported.splitlines()
    if "nonebot2" in exported or "nonebot-adapter-qq" in exported:
        raise ValueError("NoneBot dependencies must not be present in the QQ gateway")
    if "qq-botpy==1.2.1" not in lines:
        raise ValueError("The exported dependency set must pin qq-botpy==1.2.1")
    if "--hash=" in exported or "git+" in exported:
        raise ValueError("Expected hash-free registry dependencies")
    args.output.write_text(
        exported.rstrip() + f"\n# QQ SDK: {source['repository']}=={source['version']}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
