from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.core.contracts import AgentRuntimeToolResult
from backend.app.agent_runtime.token_estimation import truncate_to_token_bound
from backend.app.agents.memory_policy import WorkingMemoryPolicy
from backend.app.agents.models import AgentProfile
from backend.app.memory.configuration import initial_embedding_status
from backend.app.memory.content import memory_content_fingerprint
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.runs.models import AgentRun
from backend.app.security.redaction import (
    redact_sensitive_payload,
    redact_sensitive_text,
    redact_text_fragments,
)
from backend.app.tasks.models import Task, TaskStep

WORKING_MEMORY_LAYER = "working"
WORKING_MEMORY_SOURCE = "run_working_memory"


class AgentWorkingMemoryService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def prepare_run(
        self,
        *,
        run: AgentRun,
        profile: AgentProfile,
        task: Task | None,
        session_key: str | None,
        policy: WorkingMemoryPolicy,
    ) -> list[WorkspaceMemoryEntry]:
        if not policy.enabled:
            return []
        entries: list[WorkspaceMemoryEntry] = []
        if task is not None:
            entries.append(
                self.put(
                    run=run,
                    profile=profile,
                    key="objective",
                    title="Current objective",
                    content="\n".join(part for part in (task.title, task.description) if part),
                    entry_type="objective",
                    metadata={"task_id": str(task.id)},
                    session_key=session_key,
                    policy=policy,
                )
            )
            plan = self._plan_for_run(run, task)
            if plan:
                entries.append(
                    self.put(
                        run=run,
                        profile=profile,
                        key="plan",
                        title="Current execution plan",
                        content=json.dumps(plan, ensure_ascii=False, sort_keys=True, default=str),
                        entry_type="plan",
                        metadata={"task_id": str(task.id)},
                        session_key=session_key,
                        policy=policy,
                    )
                )
        return entries

    def put(
        self,
        *,
        run: AgentRun,
        profile: AgentProfile,
        key: str,
        title: str,
        content: str,
        entry_type: str,
        metadata: dict[str, object],
        session_key: str | None,
        policy: WorkingMemoryPolicy,
    ) -> WorkspaceMemoryEntry:
        if run.workspace_id != profile.workspace_id:
            raise ValueError("Working memory profile workspace mismatch")
        normalized_key = _bounded_key(key)
        safe_content = truncate_to_token_bound(
            redact_text_fragments(content.strip()),
            policy.max_entry_tokens,
        )
        if not safe_content:
            raise ValueError("Working memory content is required")
        existing = self._session.scalar(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == run.workspace_id,
                WorkspaceMemoryEntry.memory_layer == WORKING_MEMORY_LAYER,
                WorkspaceMemoryEntry.scope_type == "run",
                WorkspaceMemoryEntry.scope_id == str(run.id),
                WorkspaceMemoryEntry.memory_key == normalized_key,
            )
        )
        now = datetime.now(UTC)
        safe_metadata = redact_sensitive_payload(
            {
                **metadata,
                "session_key": session_key,
                "agent_profile_id": str(profile.id) if profile.id is not None else None,
            }
        )
        fingerprint = memory_content_fingerprint(title, safe_content)
        if existing is not None:
            if existing.content_fingerprint != fingerprint:
                existing.revision += 1
            existing.title = redact_text_fragments(title)[:240]
            existing.content = safe_content
            existing.entry_type = entry_type[:80]
            existing.content_fingerprint = fingerprint
            existing.memory_metadata = safe_metadata
            existing.expires_at = now + timedelta(seconds=policy.ttl_seconds)
            existing.status = "active"
            existing.archived_at = None
            self._session.flush([existing])
            return existing
        count = int(
            self._session.scalar(
                select(func.count(WorkspaceMemoryEntry.id)).where(
                    WorkspaceMemoryEntry.workspace_id == run.workspace_id,
                    WorkspaceMemoryEntry.memory_layer == WORKING_MEMORY_LAYER,
                    WorkspaceMemoryEntry.scope_type == "run",
                    WorkspaceMemoryEntry.scope_id == str(run.id),
                    WorkspaceMemoryEntry.status == "active",
                )
            )
            or 0
        )
        if count >= policy.max_entries:
            raise ValueError("Working memory entry limit reached for run")
        entry = WorkspaceMemoryEntry(
            workspace_id=run.workspace_id,
            created_by_agent_profile_id=profile.id,
            created_by_agent_run_id=run.id,
            source_type=WORKING_MEMORY_SOURCE,
            source_id=str(run.id),
            memory_layer=WORKING_MEMORY_LAYER,
            scope_type="run",
            scope_id=str(run.id),
            memory_key=normalized_key,
            entry_type=entry_type[:80],
            title=redact_text_fragments(title)[:240],
            content=safe_content,
            tags=["working_memory", f"run:{run.id}"],
            visibility_scope="run",
            importance=0,
            status="active",
            revision=1,
            content_fingerprint=fingerprint,
            memory_metadata=safe_metadata,
            expires_at=now + timedelta(seconds=policy.ttl_seconds),
        )
        self._session.add(entry)
        self._session.flush([entry])
        return entry

    def record_tool_result(
        self,
        *,
        context_workspace_id: UUID,
        run_id: UUID,
        tool_name: str,
        tool_call_id: str | None,
        result: AgentRuntimeToolResult,
        policy: WorkingMemoryPolicy,
    ) -> WorkspaceMemoryEntry | None:
        run = self._session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == context_workspace_id,
                AgentRun.id == run_id,
            )
        )
        if run is None or run.agent_profile_id is None or not policy.enabled:
            return None
        profile = self._session.scalar(
            select(AgentProfile).where(
                AgentProfile.workspace_id == context_workspace_id,
                AgentProfile.id == run.agent_profile_id,
            )
        )
        if profile is None:
            return None
        payload = redact_sensitive_payload(
            {
                "status": result.status,
                "output": result.output,
                "error": result.error,
            }
        )
        return self.put(
            run=run,
            profile=profile,
            key=f"tool:{tool_call_id or tool_name}",
            title=f"Tool result: {tool_name}",
            content=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
            entry_type="tool_result",
            metadata={"tool_name": tool_name, "tool_call_id": tool_call_id},
            session_key=None,
            policy=policy,
        )

    def active_for_run(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
    ) -> list[WorkspaceMemoryEntry]:
        now = datetime.now(UTC)
        entries = list(
            self._session.scalars(
                select(WorkspaceMemoryEntry)
                .where(
                    WorkspaceMemoryEntry.workspace_id == workspace_id,
                    WorkspaceMemoryEntry.memory_layer == WORKING_MEMORY_LAYER,
                    WorkspaceMemoryEntry.scope_type == "run",
                    WorkspaceMemoryEntry.scope_id == str(run_id),
                    WorkspaceMemoryEntry.status == "active",
                )
                .order_by(WorkspaceMemoryEntry.created_at.asc(), WorkspaceMemoryEntry.id.asc())
            ).all()
        )
        active: list[WorkspaceMemoryEntry] = []
        for entry in entries:
            if entry.expires_at is not None and _as_utc(entry.expires_at) <= now:
                entry.status = "expired"
                entry.archived_at = now
            else:
                active.append(entry)
        self._session.flush(entries)
        return active

    def promote(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        memory_entry_id: UUID,
    ) -> WorkspaceMemoryEntry:
        source = self._session.scalar(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.id == memory_entry_id,
                WorkspaceMemoryEntry.memory_layer == WORKING_MEMORY_LAYER,
                WorkspaceMemoryEntry.scope_type == "run",
                WorkspaceMemoryEntry.scope_id == str(run_id),
            )
        )
        if source is None:
            raise ValueError("Working memory entry not found in current run")
        existing = self._session.scalar(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == workspace_id,
                WorkspaceMemoryEntry.memory_layer == "episodic",
                WorkspaceMemoryEntry.source_type == "working_memory_promotion",
                WorkspaceMemoryEntry.source_id == str(source.id),
            )
        )
        if existing is not None:
            return existing
        if source.status != "active":
            raise ValueError("Only active working memory can be promoted")
        run = self._session.scalar(
            select(AgentRun).where(
                AgentRun.workspace_id == workspace_id,
                AgentRun.id == run_id,
            )
        )
        if run is None:
            raise ValueError("Working memory run not found")
        scope_type = "task" if run.task_id is not None else "agent"
        scope_id = str(run.task_id or run.agent_profile_id or run.id)
        promoted = WorkspaceMemoryEntry(
            workspace_id=workspace_id,
            created_by_agent_profile_id=run.agent_profile_id,
            created_by_agent_run_id=run.id,
            source_type="working_memory_promotion",
            source_id=str(source.id),
            memory_layer="episodic",
            scope_type=scope_type,
            scope_id=scope_id,
            memory_key=f"working:{source.id}",
            entry_type=source.entry_type,
            title=source.title,
            content=source.content,
            tags=list(dict.fromkeys([*source.tags, "promoted"])),
            visibility_scope=scope_type,
            importance=max(1, source.importance),
            status="active",
            revision=1,
            content_fingerprint=source.content_fingerprint,
            memory_metadata={
                **dict(source.memory_metadata),
                "promotion": {
                    "source_memory_id": str(source.id),
                    "source_run_id": str(run.id),
                    "promoted_at": datetime.now(UTC).isoformat(),
                },
            },
            embedding_status=initial_embedding_status(self._session, workspace_id),
        )
        self._session.add(promoted)
        self._session.flush([promoted])
        source.status = "promoted"
        source.archived_at = datetime.now(UTC)
        source.memory_metadata = {
            **dict(source.memory_metadata),
            "promoted_to_memory_id": str(promoted.id),
        }
        self._session.flush([source])
        return promoted

    def expire_run(self, *, workspace_id: UUID, run_id: UUID) -> int:
        entries = self.active_for_run(workspace_id=workspace_id, run_id=run_id)
        now = datetime.now(UTC)
        for entry in entries:
            entry.status = "expired"
            entry.archived_at = now
        self._session.flush(entries)
        return len(entries)

    def _plan_for_run(self, run: AgentRun, task: Task) -> dict[str, object]:
        plan: dict[str, object] = {}
        if isinstance(task.project_plan, dict) and task.project_plan:
            plan["project_plan"] = redact_sensitive_payload(task.project_plan)
        if run.task_step_id is not None:
            step = self._session.scalar(
                select(TaskStep).where(
                    TaskStep.workspace_id == run.workspace_id,
                    TaskStep.task_id == task.id,
                    TaskStep.id == run.task_step_id,
                )
            )
            if step is not None:
                plan["current_step"] = {
                    "id": str(step.id),
                    "title": redact_sensitive_text(step.title),
                    "description": redact_sensitive_text(step.description),
                    "acceptance_criteria": [
                        redact_sensitive_text(item) for item in step.acceptance_criteria
                    ],
                    "expected_artifacts": [
                        redact_sensitive_text(item) for item in step.expected_artifacts
                    ],
                }
        return plan


def working_memory_context(entries: list[WorkspaceMemoryEntry]) -> str:
    if not entries:
        return ""
    lines = ["Current run working memory:"]
    for entry in entries:
        lines.append(f"- {entry.title}: {entry.content}")
    return "\n".join(lines)


def _bounded_key(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Working memory key is required")
    return normalized[:160]


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
