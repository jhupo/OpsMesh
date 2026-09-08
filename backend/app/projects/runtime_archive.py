from __future__ import annotations

import io
import json
import tarfile
from pathlib import PurePosixPath
from uuid import UUID

from backend.app.projects.run_manifest import RunProjectManifest, public_run_project_manifest

INTERNAL_PROJECT_DIRECTORY = ".opsmesh"


def build_runtime_project_archive(
    *,
    run_id: UUID,
    snapshot_id: UUID,
    fingerprint_sha256: str,
    manifest: RunProjectManifest,
    file_contents: dict[UUID, bytes],
) -> bytes:
    prefix = PurePosixPath("runs", str(run_id))
    files: dict[PurePosixPath, tuple[bytes, int]] = {}
    for item in manifest.files:
        content = file_contents.get(item.project_file_id)
        if content is None:
            raise ValueError("Runtime project archive is missing an input file")
        files[prefix / item.project_path] = (
            content,
            0o444 if item.access_mode == "read_only" else 0o644,
        )
    runtime_contract = {
        "snapshot_id": str(snapshot_id),
        "fingerprint_sha256": fingerprint_sha256,
        **public_run_project_manifest(manifest),
    }
    files[prefix / INTERNAL_PROJECT_DIRECTORY / "project.json"] = (
        json.dumps(
            runtime_contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        0o444,
    )

    directories = _project_directories(prefix, manifest, tuple(files))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path, mode in sorted(directories.items(), key=lambda item: item[0].as_posix()):
            info = _tar_info(path.as_posix(), mode=mode)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        for path, (content, mode) in sorted(files.items(), key=lambda item: item[0].as_posix()):
            info = _tar_info(path.as_posix(), mode=mode)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _project_directories(
    prefix: PurePosixPath,
    manifest: RunProjectManifest,
    file_paths: tuple[PurePosixPath, ...],
) -> dict[PurePosixPath, int]:
    directories: dict[PurePosixPath, int] = {
        PurePosixPath("runs"): 0o755,
        prefix: 0o755,
        prefix / INTERNAL_PROJECT_DIRECTORY: 0o555,
        prefix / manifest.input_path: 0o555,
        prefix / manifest.work_path: 0o755,
        prefix / manifest.output_path: 0o755,
    }
    for file_path in file_paths:
        parent = file_path.parent
        while parent != PurePosixPath(".") and parent not in directories:
            directories[parent] = 0o555
            parent = parent.parent
    for item in manifest.files:
        if item.access_mode != "copy_on_write":
            continue
        parent = (prefix / item.project_path).parent
        input_root = prefix / manifest.input_path
        while parent == input_root or input_root in parent.parents:
            directories[parent] = 0o755
            if parent == input_root:
                break
            parent = parent.parent
    return directories


def _tar_info(name: str, *, mode: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.mode = mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    return info
