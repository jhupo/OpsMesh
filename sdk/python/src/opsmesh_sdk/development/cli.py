"""The ``opsmesh-sdk`` author CLI: validate, build, run and test commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import JsonValue

from opsmesh_sdk.development.build import build_package
from opsmesh_sdk.development.runner import ContractRunner
from opsmesh_sdk.extensions.operations import Operation, OperationResult, declare_operation
from opsmesh_sdk.manifest import PackageManifest, validate_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opsmesh-sdk")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a package manifest")
    validate.add_argument("manifest", type=Path)

    build = subparsers.add_parser("build", help="build the installable package archive")
    build.add_argument("source", type=Path)
    build.add_argument("--target", type=Path, required=True)

    test = subparsers.add_parser("test", help="run declared contract checks")
    test.add_argument("manifest", type=Path)

    args = parser.parse_args(argv)
    if args.command == "validate":
        manifest = validate_manifest(json.loads(args.manifest.read_text()))
        print(f"manifest valid: {manifest.package_name}@{manifest.version}")
        print(f"exports: {len(manifest.export_ids)}")
        return 0
    if args.command == "build":
        target = build_package(args.source, args.target)
        print(f"package built: {target}")
        return 0
    if args.command == "test":
        manifest = validate_manifest(json.loads(args.manifest.read_text()))
        runner = ContractRunner(_fixture_operations(manifest))
        print(f"contracts checked for {manifest.package_name}@{manifest.version}")
        print(f"tool schemas: {len(runner.tool_schemas())}")
        return 0
    parser.error(f"unknown command {args.command!r}")
    return 2


def _fixture_operations(manifest: PackageManifest) -> dict[str, Operation]:
    """Declared operations with a no-op handler, for contract checks only."""
    operations: dict[str, Operation] = {}
    for operation in manifest.operations:
        declared = declare_operation(
            operation.export_id,
            description=operation.description,
            input_schema=operation.input_schema.model_dump_schema(),
            output_schema=operation.output_schema.model_dump_schema(),
            effects=operation.effects,
            timeout_seconds=operation.timeout_seconds,
            permissions=operation.permissions,
        )

        async def handler(arguments: dict[str, JsonValue]) -> OperationResult:
            return OperationResult(value={"checked": True, "arguments": arguments}, effects=())

        operations[operation.export_id] = declared(handler)
    return operations


if __name__ == "__main__":
    sys.exit(main())
