"""Crash-safe local recovery state for self-hosted MCP execution."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from .connector_models import (
    ConnectorStateError,
    JobPhase,
    McpJob,
    McpJobCompletion,
    PendingMcpJob,
)

_SCHEMA_VERSION = 1
_PHASES = {"claimed", "executing", "result_ready"}


class ConnectorStateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            os.chmod(path.parent, 0o700)
        self._connection = sqlite3.connect(path)
        try:
            os.chmod(path, 0o600)
            self._initialize()
        except Exception:
            self._connection.close()
            raise

    def remember_claimed(self, job: McpJob) -> None:
        encoded_request = _encode(job.request_payload)
        now = _now()
        with self._connection:
            row = self._connection.execute(
                "SELECT request_payload FROM mcp_job_recovery WHERE job_id = ?",
                (str(job.id),),
            ).fetchone()
            if row is not None:
                if row[0] != encoded_request:
                    raise ConnectorStateError("Recovered MCP job payload changed")
                return
            self._connection.execute(
                """
                INSERT INTO mcp_job_recovery (
                    job_id, request_payload, phase, completion_payload, updated_at
                ) VALUES (?, ?, 'claimed', NULL, ?)
                """,
                (str(job.id), encoded_request, now),
            )

    def next_pending(self) -> PendingMcpJob | None:
        row = self._connection.execute(
            """
            SELECT job_id, request_payload, phase, completion_payload
            FROM mcp_job_recovery
            ORDER BY updated_at ASC, job_id ASC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        job_id, raw_request, raw_phase, raw_completion = row
        try:
            request_payload = json.loads(raw_request)
            parsed_id = UUID(job_id)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConnectorStateError("Connector recovery job is invalid") from exc
        if not isinstance(request_payload, dict) or raw_phase not in _PHASES:
            raise ConnectorStateError("Connector recovery job is invalid")
        phase = cast(JobPhase, raw_phase)
        completion = None
        if raw_completion is not None:
            try:
                completion = McpJobCompletion.from_payload(json.loads(raw_completion))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ConnectorStateError("Connector recovery completion is invalid") from exc
        if phase == "result_ready" and completion is None:
            raise ConnectorStateError("Connector recovery result is missing")
        return PendingMcpJob(
            job=McpJob(id=parsed_id, request_payload=request_payload),
            phase=phase,
            completion=completion,
        )

    def mark_executing(self, job_id: UUID) -> None:
        self._transition(job_id, from_phases=("claimed",), to_phase="executing")

    def mark_completion(self, job_id: UUID, completion: McpJobCompletion) -> None:
        with self._connection:
            updated = self._connection.execute(
                """
                UPDATE mcp_job_recovery
                SET phase = 'result_ready', completion_payload = ?, updated_at = ?
                WHERE job_id = ? AND phase IN ('claimed', 'executing')
                """,
                (_encode(completion.to_payload()), _now(), str(job_id)),
            )
            if updated.rowcount != 1:
                raise ConnectorStateError("Connector recovery completion transition failed")

    def remove(self, job_id: UUID) -> None:
        with self._connection:
            self._connection.execute(
                "DELETE FROM mcp_job_recovery WHERE job_id = ?",
                (str(job_id),),
            )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ConnectorStateStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _initialize(self) -> None:
        version_row = self._connection.execute("PRAGMA user_version").fetchone()
        version = int(version_row[0]) if version_row is not None else 0
        if version not in {0, _SCHEMA_VERSION}:
            raise ConnectorStateError("Unsupported connector state schema")
        with self._connection:
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS mcp_job_recovery (
                    job_id TEXT PRIMARY KEY,
                    request_payload TEXT NOT NULL,
                    phase TEXT NOT NULL CHECK (phase IN ('claimed', 'executing', 'result_ready')),
                    completion_payload TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def _transition(
        self,
        job_id: UUID,
        *,
        from_phases: tuple[JobPhase, ...],
        to_phase: JobPhase,
    ) -> None:
        placeholders = ", ".join("?" for _ in from_phases)
        with self._connection:
            updated = self._connection.execute(
                f"""
                UPDATE mcp_job_recovery
                SET phase = ?, updated_at = ?
                WHERE job_id = ? AND phase IN ({placeholders})
                """,
                (to_phase, _now(), str(job_id), *from_phases),
            )
            if updated.rowcount != 1:
                raise ConnectorStateError("Connector recovery state transition failed")


def _encode(value: dict[str, object]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ConnectorStateError("Connector recovery payload is not JSON serializable") from exc


def _now() -> str:
    return datetime.now(UTC).isoformat()
