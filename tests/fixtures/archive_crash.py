"""Exit during the real export write after its running state has committed."""

import os
import sys
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from opsmesh.bootstrap.models import register_models
from opsmesh.resources.storage.backends import LocalObjectStorage
from opsmesh.resources.transfers.archive_jobs import WorkspaceArchiveExportJobService
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest


class CrashStorage(LocalObjectStorage):
    def write(self, storage_key: str, content: bytes) -> None:
        super().write(storage_key, content)
        os._exit(23)


if __name__ == "__main__":
    register_models()
    engine = create_engine(sys.argv[1])
    with Session(engine) as session:
        WorkspaceArchiveExportJobService(session).run_archive_export_job(
            request=ExecutionRequest(
                execution_id=UUID(sys.argv[4]),
                resource_id=UUID(sys.argv[4]),
                workspace_id=UUID(sys.argv[2]),
                requested_by_user_id=UUID(sys.argv[3]),
                kind=ExecutionKind.ARCHIVE_EXPORT,
            ),
            storage=CrashStorage(sys.argv[5]),
        )
