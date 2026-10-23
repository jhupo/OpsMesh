"""Offline plugin package development commands."""

import argparse
from pathlib import Path

from opsmesh_sdk.packages import build_package, inspect_package


def main() -> None:
    parser = argparse.ArgumentParser(prog="opsmesh-plugin")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("source", type=Path)
    build.add_argument("--output", type=Path, required=True)
    check = commands.add_parser("check")
    check.add_argument("package", type=Path)
    args = parser.parse_args()
    manifest = (
        build_package(args.source, args.output)
        if args.command == "build"
        else inspect_package(args.package)
    )
    print(manifest.model_dump_json(indent=2))
