"""Terminate at a real external write after the durable restore receipt commits."""

import os
import sys
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from opsmesh.bootstrap.models import register_models
from opsmesh.resources.storage.backends import LocalObjectStorage
from opsmesh.resources.transfers.archive_restore_drill import WorkspaceArchiveRestoreDrillService
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest


class CrashStorage(LocalObjectStorage):
    def write(self, storage_key: str, content: bytes) -> None:
        super().write(storage_key, content)
        os._exit(23)


if __name__ == "__main__":
    register_models()
    engine = create_engine(sys.argv[1])
    with Session(engine) as session:
        WorkspaceArchiveRestoreDrillService(session).run(
            execution=ExecutionRequest(
                execution_id=UUID(sys.argv[4]),
                workspace_id=UUID(sys.argv[2]),
                requested_by_user_id=UUID(sys.argv[3]),
                resource_id=UUID(sys.argv[4]),
                kind=ExecutionKind.ARCHIVE_RESTORE_DRILL,
            ),
            storage=CrashStorage(sys.argv[5]),
        )
