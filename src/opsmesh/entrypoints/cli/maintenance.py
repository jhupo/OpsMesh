"""Small CLI adapter for offline maintenance; never starts background workers."""

import argparse
import json
from pathlib import Path

from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.framework.errors import OpsMeshError
from opsmesh.maintenance.backup import create_backup, restore_backup, verify_backup


async def execute_maintenance(arguments: argparse.Namespace) -> None:
    path = Path(arguments.path)
    try:
        if arguments.command == "backup-verify":
            manifest = await verify_backup(path, expected_manifest_digest=arguments.manifest_digest)
        elif arguments.command == "backup":
            manifest = await create_backup(
                OpsMeshSettings.from_environment(),
                path,
                maintenance=arguments.maintenance,
                timeout_seconds=arguments.timeout_seconds,
            )
        else:
            manifest = await restore_backup(
                OpsMeshSettings.from_environment(),
                path,
                expected_manifest_digest=arguments.manifest_digest,
                maintenance=arguments.maintenance,
                timeout_seconds=arguments.timeout_seconds,
            )
    except OSError as error:
        raise OpsMeshError("BACKUP_IO_FAILED", "Backup filesystem operation failed") from error
    print(
        json.dumps(
            {
                "operation": arguments.command,
                "bundle": str(path.resolve()),
                "manifest_digest": manifest.manifest_digest,
                "database_kind": manifest.database_kind,
                "workspace_files": len(manifest.files),
            }
        )
    )
