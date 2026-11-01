"""Public worker diagnostics expose bounded platform facts, never arbitrary stdout."""

import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, ValidationError

from opsmesh.framework.typing import OpsMeshContract
from opsmesh.infrastructure.run_workers.status import WorkerMode, WorkerProcessStatus
from opsmesh.modules.runtime.api import RunStatus


class RunWorkerView(OpsMeshContract):
    run_id: str
    mode: WorkerMode
    status: Literal["running", "exited"]
    pid: int | None = None
    exit_code: int | None = None
    job_name: str | None = None
    reason: str | None = None

    @classmethod
    def from_process(cls, process: WorkerProcessStatus) -> "RunWorkerView":
        value = process.model_dump(exclude={"launch_token"})
        if value["reason"] and not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value["reason"]):
            value["reason"] = "WorkerStateChanged"
        return cls.model_validate(value)


class WorkerDiagnosticEntry(OpsMeshContract):
    event: Literal["worker.started", "worker.finished", "worker.error"]
    occurred_at: datetime
    run_id: str
    status: RunStatus | None = None
    code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,63}$")


class WorkerDiagnosticsView(OpsMeshContract):
    run_id: str
    entries: tuple[WorkerDiagnosticEntry, ...] = ()
    omitted_lines: int = 0
    truncated: bool = False


def public_diagnostics(raw: str, run_id: str, limit_bytes: int) -> WorkerDiagnosticsView:
    data = raw.encode("utf-8")
    truncated = len(data) >= limit_bytes
    lines = data[-limit_bytes:].decode("utf-8", errors="replace").splitlines()
    entries = []
    omitted = 0
    for line in lines:
        prefix, _, remainder = line.partition(" ")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.+Z-]+", prefix):
            line = remainder  # Kubernetes timestamps=true adds an RFC3339 prefix.
        try:
            value: Any = json.loads(line)
            if not isinstance(value, dict) or value.get("run_id") != run_id:
                raise ValueError("Not a diagnostic for this Run")
            selected = {
                key: value[key] for key in WorkerDiagnosticEntry.model_fields if key in value
            }
            entry = WorkerDiagnosticEntry.model_validate(selected)
        except (ValueError, ValidationError):
            omitted += 1
            continue
        entries.append(entry)
    if len(entries) > 1000:
        omitted += len(entries) - 1000
        entries = entries[-1000:]
        truncated = True
    return WorkerDiagnosticsView(
        run_id=run_id,
        entries=tuple(entries),
        omitted_lines=omitted,
        truncated=truncated,
    )
