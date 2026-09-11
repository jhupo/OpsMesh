from __future__ import annotations

from collections import Counter
from uuid import UUID

from backend.app.agents.models import AgentProfile
from backend.app.files.artifact_models import Artifact
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.tasks.observation_cards import (
    TaskObservationArtifactCards,
    TaskObservationOverviewCards,
    TaskObservationReviewCards,
    TaskObservationTimelineCards,
    agent_payload,
    step_card,
)
from backend.app.tasks.observation_domain import TaskObservationDomainCards
from backend.app.tasks.observation_quality_cards import TaskObservationQualityCards


class TaskObservationSectionBuilder:
    def __init__(self) -> None:
        self._domain_cards = TaskObservationDomainCards()
        self._overview_cards = TaskObservationOverviewCards()
        self._timeline_cards = TaskObservationTimelineCards()
        self._artifact_cards = TaskObservationArtifactCards()
        self._review_cards = TaskObservationReviewCards()
        self._quality_cards = TaskObservationQualityCards()

    def sections(
        self,
        *,
        task: Task,
        view_type: str,
        steps: list[TaskStep],
        runs: list[AgentRun],
        messages: list[TaskMessage],
        artifacts: list[Artifact],
        run_events: list[RunEvent],
        agents: dict[UUID, AgentProfile],
    ) -> list[dict[str, object]]:
        return [
            self.overview_section(task, steps, runs, agents),
            self.timeline_section(messages, run_events),
            self.artifact_section(artifacts),
            self.review_section(task, messages),
            self.quality_section(steps, messages),
            self._domain_cards.domain_section(task, view_type, artifacts),
        ]

    def overview_section(
        self,
        task: Task,
        steps: list[TaskStep],
        runs: list[AgentRun],
        agents: dict[UUID, AgentProfile],
    ) -> dict[str, object]:
        return {
            "key": "overview",
            "title": "Overview",
            "cards": self._overview_cards.build(task, steps, runs, agents),
        }

    def timeline_section(
        self,
        messages: list[TaskMessage],
        run_events: list[RunEvent],
    ) -> dict[str, object]:
        return {
            "key": "timeline",
            "title": "Timeline",
            "cards": self._timeline_cards.build(messages, run_events),
        }

    def artifact_section(self, artifacts: list[Artifact]) -> dict[str, object]:
        return {
            "key": "artifacts",
            "title": "Artifacts",
            "cards": self._artifact_cards.build(artifacts),
        }

    def review_section(self, task: Task, messages: list[TaskMessage]) -> dict[str, object]:
        return {
            "key": "review",
            "title": "Review",
            "cards": self._review_cards.build(task, messages),
        }

    def quality_section(
        self,
        steps: list[TaskStep],
        messages: list[TaskMessage],
    ) -> dict[str, object]:
        return {
            "key": "quality",
            "title": "Quality",
            "cards": self._quality_cards.build(steps, messages),
        }

    def revision_history_cards(
        self,
        steps: list[TaskStep],
        messages: list[TaskMessage],
    ) -> list[dict[str, object]]:
        return self._quality_cards.revision_history_cards(steps, messages)

    def risk_flag_cards(
        self,
        steps: list[TaskStep],
        messages: list[TaskMessage],
    ) -> list[dict[str, object]]:
        return self._quality_cards.risk_flag_cards(steps, messages)

    def summary(
        self,
        task: Task,
        steps: list[TaskStep],
        runs: list[AgentRun],
        messages: list[TaskMessage],
        artifacts: list[Artifact],
    ) -> dict[str, object]:
        step_counts = Counter(step.status for step in steps)
        run_counts = Counter(run.status for run in runs)
        completed_steps = step_counts.get("completed", 0)
        progress = completed_steps / len(steps) if steps else 0.0
        return {
            "title": task.title,
            "status": task.status,
            "domain_type": task.domain_type,
            "priority": task.priority,
            "progress": round(progress, 4),
            "step_counts": dict(sorted(step_counts.items())),
            "run_counts": dict(sorted(run_counts.items())),
            "message_count": len(messages),
            "artifact_count": len(artifacts),
            "has_final_output": task.final_output is not None,
        }

    def step_card(
        self,
        step: TaskStep,
        agents: dict[UUID, AgentProfile],
    ) -> dict[str, object]:
        return step_card(step, agents)


__all__ = ["TaskObservationSectionBuilder", "agent_payload"]
