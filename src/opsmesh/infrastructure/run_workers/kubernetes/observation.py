"""Translate Job facts and retain bounded worker diagnostics after cluster cleanup."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.run_workers.kubernetes.client import KubernetesAPI
from opsmesh.infrastructure.run_workers.status import WorkerProcessStatus


def job_status(job: dict[str, Any], run_id: str, token: str) -> WorkerProcessStatus:
    conditions = job.get("status", {}).get("conditions", [])
    terminal = next(
        (
            item
            for item in conditions
            if item.get("status") == "True" and item.get("type") in {"Complete", "Failed"}
        ),
        None,
    )
    return WorkerProcessStatus(
        run_id=run_id,
        mode="kubernetes",
        job_name=job["metadata"]["name"],
        launch_token=token,
        status="exited" if terminal else "running",
        exit_code=(0 if terminal["type"] == "Complete" else 1) if terminal else None,
        reason=terminal.get("reason", terminal["type"]) if terminal else None,
    )


class WorkerDiagnostics:
    def __init__(self, directory: Path, api: KubernetesAPI) -> None:
        self.directory, self.api = directory / "worker-logs", api

    def _directory(self, run_id: str) -> Path:
        return self.directory / hashlib.sha256(run_id.encode()).hexdigest()

    async def collect(self, job: dict[str, Any]) -> tuple[str, int | None]:
        entries, exit_code = [], None
        for pod in await self.api.pods(job["metadata"]["uid"]):
            name = pod["metadata"]["name"]
            for container in pod.get("status", {}).get("containerStatuses", []):
                if container.get("name") == "run":
                    exit_code = container.get("state", {}).get("terminated", {}).get("exitCode")
            try:
                content = await self.api.logs(name)
            except OpsMeshError as error:
                if error.retryable:
                    raise
                content = f"[pod log unavailable: {error.code}]\n"
            entries.append(f"--- {name} ---\n{content}")
        content = "\n".join(entries) or "[Job has no remaining worker Pods]\n"
        return self.bounded(content), exit_code

    def bounded(self, content: str) -> str:
        return content.encode("utf-8")[-self.api.configuration.log_limit_bytes :].decode(
            "utf-8", errors="replace"
        )

    def persist(self, status: WorkerProcessStatus, generation: int, logs: str) -> None:
        directory = self._directory(status.run_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink():
            raise OpsMeshError("PATH_INVALID", "Worker log directory cannot be a symlink")
        directory.chmod(0o700)
        # Each generation is replaced, not appended, so two coordinators never duplicate output.
        for suffix, content in (("log", logs), ("json", status.model_dump_json())):
            destination = directory / f"{generation:08d}.{suffix}"
            temporary = directory / f"{generation:08d}.{uuid4().hex}.tmp"
            try:
                temporary.write_text(content, encoding="utf-8")
                os.chmod(temporary, 0o600)
                temporary.replace(destination)
                os.chmod(destination, 0o600)
            finally:
                temporary.unlink(missing_ok=True)

    def saved_status(self, run_id: str) -> WorkerProcessStatus | None:
        records = sorted(self._directory(run_id).glob("*.json"))
        if not records:
            return None
        return WorkerProcessStatus.model_validate(json.loads(records[-1].read_text("utf-8")))

    def saved_logs(self, run_id: str) -> str:
        remaining = self.api.configuration.log_limit_bytes
        entries = []
        for path in sorted(self._directory(run_id).glob("*.log"), reverse=True):
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - remaining))
                value = stream.read(remaining)
            entries.append(value.decode("utf-8", errors="replace"))
            remaining -= len(value)
            if remaining <= 0:
                break
        return "\n".join(reversed(entries))
