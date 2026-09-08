from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.contracts import AgentRunResult
from backend.app.agents.models import AgentProfile
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.runs.models import AgentRun
from backend.app.security.redaction import redact_text_fragments
from backend.app.tasks.models import Task

RUN_SUMMARY_ENTRY_TYPE = "agent_run_summary"
RUN_SUMMARY_SOURCE_TYPE = "agent_run"


class AgentRunMemoryCaptureService:
    """Captures opt-in long-term memory entries from completed agent runs."""

    def __init__(self, db_session: Session) -> None:
        self._db_session = db_session

    def capture_completed_run(
        self,
        run: AgentRun,
        result: AgentRunResult,
        *,
        profile: AgentProfile | None = None,
        task: Task | None = None,
    ) -> WorkspaceMemoryEntry | None:
        profile = profile or self._profile_for_run(run)
        if profile is None or profile.memory_policy.get("auto_capture") is not True:
            return None
        if profile.workspace_id != run.workspace_id:
            return None

        existing = self._existing_capture(run)
        if existing is not None:
            return existing

        task = task or self._task_for_run(run)
        title = _summary_title(run=run, profile=profile, task=task)
        content = _summary_content(result)
        entry = WorkspaceMemoryEntry(
            workspace_id=run.workspace_id,
            created_by_agent_profile_id=profile.id,
            created_by_agent_run_id=run.id,
            source_type=RUN_SUMMARY_SOURCE_TYPE,
            source_id=str(run.id),
            entry_type=RUN_SUMMARY_ENTRY_TYPE,
            title=title,
            content=content,
            tags=_summary_tags(profile=profile, task=task),
            visibility_scope="workspace",
            importance=_capture_importance(profile.memory_policy),
            status="active",
            memory_metadata=_summary_metadata(
                run=run,
                profile=profile,
                task=task,
                content=content,
            ),
        )
        self._db_session.add(entry)
        self._db_session.flush([entry])
        return entry

    def _profile_for_run(self, run: AgentRun) -> AgentProfile | None:
        if run.agent_profile_id is None:
            return None
        return self._db_session.get(AgentProfile, run.agent_profile_id)

    def _task_for_run(self, run: AgentRun) -> Task | None:
        if run.task_id is None:
            return None
        task = self._db_session.get(Task, run.task_id)
        if task is None or task.workspace_id != run.workspace_id:
            return None
        return task

    def _existing_capture(self, run: AgentRun) -> WorkspaceMemoryEntry | None:
        return self._db_session.scalar(
            select(WorkspaceMemoryEntry).where(
                WorkspaceMemoryEntry.workspace_id == run.workspace_id,
                WorkspaceMemoryEntry.entry_type == RUN_SUMMARY_ENTRY_TYPE,
                WorkspaceMemoryEntry.source_type == RUN_SUMMARY_SOURCE_TYPE,
                WorkspaceMemoryEntry.source_id == str(run.id),
            )
        )


def _summary_title(
    *,
    run: AgentRun,
    profile: AgentProfile,
    task: Task | None,
) -> str:
    if task is not None and task.title:
        title = f"Run summary: {task.title}"
    else:
        title = f"Run summary: {profile.name or profile.role or run.id}"
    return redact_text_fragments(title)[:240]


def _summary_content(result: AgentRunResult) -> str:
    content = redact_text_fragments(" ".join(result.final_output.split()))
    return content or "Agent run completed without textual output."


def _summary_tags(*, profile: AgentProfile, task: Task | None) -> list[str]:
    tags = ["agent_run_summary", f"agent_role:{profile.role}"]
    if task is not None:
        tags.append(f"task:{task.id}")
        if task.domain_type:
            tags.append(f"domain:{task.domain_type}")
    return tags


def _summary_metadata(
    *,
    run: AgentRun,
    profile: AgentProfile,
    task: Task | None,
    content: str,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "capture_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "source": "run_completion",
        "run": {
            "id": str(run.id),
            "status": run.status,
            "task_step_id": str(run.task_step_id) if run.task_step_id is not None else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        },
        "agent": {
            "profile_id": str(profile.id) if profile.id is not None else None,
            "name": redact_text_fragments(profile.name) if profile.name else profile.name,
            "role": redact_text_fragments(profile.role),
            "version": profile.version,
        },
        "content_chars": len(content),
    }
    if task is not None:
        metadata["task"] = {
            "id": str(task.id),
            "title": redact_text_fragments(task.title),
            "status": task.status,
            "domain_type": redact_text_fragments(task.domain_type)
            if task.domain_type
            else task.domain_type,
        }
    return metadata


def _capture_importance(policy: dict[str, object]) -> int:
    value = policy.get("capture_importance")
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, min(value, 10))
    return 1
