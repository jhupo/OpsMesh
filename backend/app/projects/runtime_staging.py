from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from backend.app.files.storage import ObjectStorage, StorageObjectTooLargeError
from backend.app.projects.models import AgentRunProjectSnapshot
from backend.app.projects.run_manifest import RunProjectManifest
from backend.app.projects.runtime_archive import build_runtime_project_archive
from backend.app.projects.runtime_io_errors import ProjectRunIOError
from backend.app.runs.models import AgentRun


@dataclass(frozen=True, slots=True)
class ProjectInputArchive:
    content: bytes
    file_count: int
    total_bytes: int


class ProjectInputArchiveBuilder:
    def __init__(self, storage: ObjectStorage) -> None:
        self._storage = storage

    def build(
        self,
        *,
        run: AgentRun,
        snapshot: AgentRunProjectSnapshot,
        manifest: RunProjectManifest,
    ) -> ProjectInputArchive:
        contents = self._load_contents(run, manifest)
        archive = build_runtime_project_archive(
            run_id=run.id,
            snapshot_id=snapshot.id,
            fingerprint_sha256=snapshot.fingerprint_sha256,
            manifest=manifest,
            file_contents=contents,
        )
        return ProjectInputArchive(
            content=archive,
            file_count=len(contents),
            total_bytes=sum(len(content) for content in contents.values()),
        )

    def _load_contents(
        self,
        run: AgentRun,
        manifest: RunProjectManifest,
    ) -> dict[UUID, bytes]:
        contents: dict[UUID, bytes] = {}
        for item in manifest.files:
            try:
                content = self._storage.read_limited(item.storage_key, item.size_bytes)
            except (FileNotFoundError, StorageObjectTooLargeError) as exc:
                raise ProjectRunIOError(
                    code="project_input_object_invalid",
                    message="A snapshotted project input is missing or has changed size",
                    stage="input_staging",
                    retryable=False,
                    metadata={"project_file_id": str(item.project_file_id)},
                ) from exc
            except Exception as exc:
                raise ProjectRunIOError(
                    code="project_input_storage_unavailable",
                    message="Project input storage is temporarily unavailable",
                    stage="input_staging",
                    retryable=True,
                    metadata={"project_file_id": str(item.project_file_id)},
                ) from exc
            if (
                len(content) != item.size_bytes
                or sha256(content).hexdigest() != item.checksum_sha256
            ):
                raise ProjectRunIOError(
                    code="project_input_integrity_failed",
                    message="A snapshotted project input failed integrity verification",
                    stage="input_staging",
                    retryable=False,
                    metadata={"project_file_id": str(item.project_file_id)},
                )
            contents[item.project_file_id] = content
        return contents
