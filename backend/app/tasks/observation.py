from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.artifacts.models import Artifact
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.security.redaction import redact_sensitive_payload
from backend.app.tasks.models import Task, TaskMessage, TaskStep

SUPPORTED_VIEW_TYPES = {"generic", "aigc", "novel", "research", "software"}


class TaskObservationService:
    """Compose a task observation payload from existing durable task records."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_observation(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        view_type: str | None = None,
    ) -> dict[str, object] | None:
        task = self._session.scalar(
            select(Task).where(Task.workspace_id == workspace_id, Task.id == task_id)
        )
        if task is None:
            return None

        resolved_view_type = self._resolve_view_type(task, view_type)
        steps = self._list_steps(workspace_id, task.id)
        runs = self._list_runs(workspace_id, task.id)
        messages = self._list_messages(workspace_id, task.id)
        artifacts = self._list_artifacts(workspace_id, task.id)
        run_events = self._list_run_events(workspace_id, runs)
        agents = self._agent_map(workspace_id, steps, runs, messages)

        return {
            "workspace_id": task.workspace_id,
            "task_id": task.id,
            "view_type": resolved_view_type,
            "generated_at": datetime.now(UTC),
            "summary": self._summary(task, steps, runs, messages, artifacts),
            "sections": self._sections(
                task=task,
                view_type=resolved_view_type,
                steps=steps,
                runs=runs,
                messages=messages,
                artifacts=artifacts,
                run_events=run_events,
                agents=agents,
            ),
        }

    def _resolve_view_type(self, task: Task, requested: str | None) -> str:
        if requested is not None and requested != "auto":
            normalized = requested.strip().lower()
            if normalized not in SUPPORTED_VIEW_TYPES:
                raise ValueError("Unsupported observation view type")
            return normalized
        domain_type = (task.domain_type or "generic").strip().lower()
        return domain_type if domain_type in SUPPORTED_VIEW_TYPES else "generic"

    def _sections(
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
            self._overview_section(task, steps, runs, agents),
            self._timeline_section(messages, run_events),
            self._artifact_section(artifacts),
            self._review_section(task, messages),
            self._quality_section(steps, messages),
            self._domain_section(task, view_type, artifacts),
        ]

    def _overview_section(
        self,
        task: Task,
        steps: list[TaskStep],
        runs: list[AgentRun],
        agents: dict[UUID, AgentProfile],
    ) -> dict[str, object]:
        active_agent_ids = {
            run.agent_profile_id
            for run in runs
            if run.agent_profile_id is not None and run.status in {"queued", "running"}
        }
        current_steps = [
            self._step_card(step, agents)
            for step in steps
            if step.status in {"queued", "running", "waiting_approval", "blocked"}
        ]
        if not current_steps and steps:
            current_steps = [self._step_card(steps[-1], agents)]

        cards: list[dict[str, object]] = [
            {
                "card_type": "task_status",
                "title": task.title,
                "status": task.status,
                "data": {
                    "domain_type": task.domain_type,
                    "priority": task.priority,
                    "description": task.description,
                    "completed_at": task.completed_at,
                },
            },
            {
                "card_type": "active_agents",
                "title": "Active agents",
                "status": "active" if active_agent_ids else "idle",
                "data": {
                    "agents": [
                        self._agent_payload(agents[agent_id])
                        for agent_id in sorted(active_agent_ids, key=str)
                        if agent_id in agents
                    ],
                },
            },
        ]
        cards.extend(current_steps)
        return {"key": "overview", "title": "Overview", "cards": cards}

    def _timeline_section(
        self,
        messages: list[TaskMessage],
        run_events: list[RunEvent],
    ) -> dict[str, object]:
        cards = [
            {
                "card_type": "task_message",
                "title": message.message_type,
                "status": self._status_from_message_type(message.message_type),
                "data": {
                    "id": str(message.id),
                    "sequence": message.sequence,
                    "body": message.body,
                    "task_step_id": self._str_or_none(message.task_step_id),
                    "agent_run_id": self._str_or_none(message.agent_run_id),
                    "agent_profile_id": self._str_or_none(message.agent_profile_id),
                    "payload": self._safe_message_payload(message.payload),
                    "created_at": message.created_at,
                },
            }
            for message in messages[-20:]
        ]
        event_counts = Counter(event.event_type for event in run_events)
        if event_counts:
            cards.append(
                {
                    "card_type": "run_event_summary",
                    "title": "Run events",
                    "status": "recorded",
                    "data": {"counts": dict(sorted(event_counts.items()))},
                }
            )
        return {"key": "timeline", "title": "Timeline", "cards": cards}

    def _artifact_section(self, artifacts: list[Artifact]) -> dict[str, object]:
        return {
            "key": "artifacts",
            "title": "Artifacts",
            "cards": [
                {
                    "card_type": "artifact",
                    "title": artifact.filename,
                    "status": "available",
                    "data": {
                        "id": str(artifact.id),
                        "artifact_type": artifact.artifact_type,
                        "content_type": artifact.content_type,
                        "size_bytes": artifact.size_bytes,
                        "checksum_sha256": artifact.checksum_sha256,
                        "agent_run_id": self._str_or_none(artifact.agent_run_id),
                        "task_step_id": self._str_or_none(artifact.task_step_id),
                        "agent_profile_id": self._str_or_none(artifact.agent_profile_id),
                        "work_package_id": artifact.work_package_id,
                        "version": artifact.version,
                        "supersedes_artifact_id": self._str_or_none(
                            artifact.supersedes_artifact_id
                        ),
                        "review_status": artifact.review_status,
                        "metadata": redact_sensitive_payload(artifact.artifact_metadata),
                        "created_at": artifact.created_at,
                    },
                }
                for artifact in artifacts
            ],
        }

    def _review_section(self, task: Task, messages: list[TaskMessage]) -> dict[str, object]:
        review_messages = [
            message
            for message in messages
            if message.message_type
            in {"pm.acceptance_decision", "pm.follow_up_created", "approval.requested"}
        ]
        cards = [
            {
                "card_type": "review_event",
                "title": message.message_type,
                "status": str(
                    message.payload.get("decision")
                    or message.payload.get("status")
                    or "recorded"
                ),
                "data": {
                    "sequence": message.sequence,
                    "body": message.body,
                    "payload": self._safe_message_payload(message.payload),
                    "created_at": message.created_at,
                },
            }
            for message in review_messages
        ]
        if task.final_output is not None:
            cards.append(
                {
                    "card_type": "final_output",
                    "title": "Final output",
                    "status": task.status,
                    "data": redact_sensitive_payload(task.final_output),
                }
            )
        return {"key": "review", "title": "Review", "cards": cards}

    def _quality_section(
        self,
        steps: list[TaskStep],
        messages: list[TaskMessage],
    ) -> dict[str, object]:
        revision_cards = self._revision_history_cards(steps, messages)
        risk_cards = self._risk_flag_cards(steps, messages)
        return {
            "key": "quality",
            "title": "Quality",
            "cards": [*revision_cards, *risk_cards],
        }

    def _revision_history_cards(
        self,
        steps: list[TaskStep],
        messages: list[TaskMessage],
    ) -> list[dict[str, object]]:
        cards: list[dict[str, object]] = []
        for step in steps:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            correction = dependencies.get("correction")
            if isinstance(correction, dict):
                cards.append(
                    {
                        "card_type": "revision_history",
                        "title": step.title,
                        "status": step.status,
                        "data": {
                            "source": "correction",
                            "task_step_id": str(step.id),
                            "work_package_id": step.work_package_id,
                            "mode": correction.get("mode"),
                            "target": redact_sensitive_payload(
                                correction.get("target")
                                if isinstance(correction.get("target"), dict)
                                else {}
                            ),
                            "instruction": correction.get("instruction"),
                            "metadata": redact_sensitive_payload(
                                correction.get("metadata")
                                if isinstance(correction.get("metadata"), dict)
                                else {}
                            ),
                            "created_at": step.created_at,
                        },
                    }
                )
            if "revision_of_work_package_id" in dependencies:
                cards.append(
                    {
                        "card_type": "revision_history",
                        "title": step.title,
                        "status": step.status,
                        "data": {
                            "source": "pm_revision",
                            "task_step_id": str(step.id),
                            "work_package_id": step.work_package_id,
                            "revision_of_work_package_id": dependencies.get(
                                "revision_of_work_package_id"
                            ),
                            "revision_cycle": dependencies.get("revision_cycle"),
                            "created_at": step.created_at,
                        },
                    }
                )
        for message in messages:
            if message.message_type != "pm.acceptance_decision":
                continue
            revision_requests = message.payload.get("revision_requests")
            if isinstance(revision_requests, list) and revision_requests:
                cards.append(
                    {
                        "card_type": "revision_history",
                        "title": "PM revision request",
                        "status": str(message.payload.get("decision") or "recorded"),
                        "data": {
                            "source": "pm_acceptance",
                            "sequence": message.sequence,
                            "revision_requests": redact_sensitive_payload(
                                {"items": revision_requests}
                            )["items"],
                            "created_at": message.created_at,
                        },
                    }
                )
        return cards

    def _risk_flag_cards(
        self,
        steps: list[TaskStep],
        messages: list[TaskMessage],
    ) -> list[dict[str, object]]:
        cards: list[dict[str, object]] = []
        for step in steps:
            dependencies = step.dependencies if isinstance(step.dependencies, dict) else {}
            reason = dependencies.get("blocked_reason")
            if isinstance(reason, str) and reason:
                cards.append(
                    {
                        "card_type": "risk_flag",
                        "title": step.title,
                        "status": "attention",
                        "data": {
                            "source": "scheduler",
                            "task_step_id": str(step.id),
                            "reason": reason,
                            "blocked_resource_keys": dependencies.get("blocked_resource_keys"),
                            "priority_score": dependencies.get("priority_score"),
                        },
                    }
                )
        for message in messages:
            if message.message_type not in {"pm.acceptance_decision", "approval.requested"}:
                continue
            payload = self._safe_message_payload(message.payload)
            risks = _risk_flags_from_payload(payload)
            for risk in risks:
                cards.append(
                    {
                        "card_type": "risk_flag",
                        "title": str(risk.get("title") or message.message_type),
                        "status": str(risk.get("severity") or "attention"),
                        "data": {
                            "source": message.message_type,
                            "sequence": message.sequence,
                            "risk": risk,
                            "created_at": message.created_at,
                        },
                    }
                )
        return cards

    def _domain_section(
        self,
        task: Task,
        view_type: str,
        artifacts: list[Artifact],
    ) -> dict[str, object]:
        builders = {
            "aigc": self._aigc_cards,
            "novel": self._novel_cards,
            "research": self._research_cards,
            "software": self._software_cards,
        }
        cards = builders.get(view_type, self._generic_domain_cards)(task, artifacts)
        return {"key": "domain", "title": f"{view_type.title()} View", "cards": cards}

    def _generic_domain_cards(
        self,
        task: Task,
        artifacts: list[Artifact],
    ) -> list[dict[str, object]]:
        return [
            {
                "card_type": "domain_state",
                "title": "Domain state",
                "status": "available" if task.domain_state else "empty",
                "data": {
                    "input": task.input,
                    "generic_state": task.generic_state,
                    "domain_state": task.domain_state,
                    "artifact_count": len(artifacts),
                },
            }
        ]

    def _aigc_cards(self, task: Task, artifacts: list[Artifact]) -> list[dict[str, object]]:
        state = task.domain_state or {}
        return [
            self._aigc_status_card(task, artifacts),
            self._domain_card("prompt", "Prompt", state),
            self._domain_card("variants", "Variants", state),
            self._domain_card("selected_asset", "Selected asset", state),
            self._domain_card("model_settings", "Model settings", state),
            self._domain_card("review_notes", "Review notes", state),
            self._domain_artifact_card(artifacts),
        ]

    def _novel_cards(self, task: Task, artifacts: list[Artifact]) -> list[dict[str, object]]:
        state = task.domain_state or {}
        return [
            self._novel_status_card(task, artifacts),
            self._domain_card("outline", "Outline", state),
            self._domain_card("chapters", "Chapters", state),
            self._domain_card("scenes", "Scenes", state),
            self._domain_card("characters", "Characters", state),
            self._domain_card("continuity_notes", "Continuity notes", state),
            self._domain_card("word_count", "Word count", state),
            self._domain_card("editorial_review", "Editorial review", state),
            self._domain_artifact_card(artifacts),
        ]

    def _research_cards(self, task: Task, artifacts: list[Artifact]) -> list[dict[str, object]]:
        state = task.domain_state or {}
        return [
            self._research_status_card(task, artifacts),
            self._domain_card("sources", "Sources", state),
            self._domain_card("claims", "Claims", state),
            self._domain_card("confidence", "Confidence", state),
            self._domain_card("citations", "Citations", state),
            self._domain_card("report_sections", "Report sections", state),
            self._domain_artifact_card(artifacts),
        ]

    def _software_cards(self, task: Task, artifacts: list[Artifact]) -> list[dict[str, object]]:
        state = task.domain_state or {}
        return [
            self._software_status_card(task, artifacts),
            self._domain_card("requirements", "Requirements", state),
            self._domain_card("design_tasks", "Design tasks", state),
            self._domain_card("branches", "Branches", state),
            self._domain_card("patches", "Patches", state),
            self._domain_card("tests", "Tests", state),
            self._domain_card("build_status", "Build status", state),
            self._domain_card("review_comments", "Review comments", state),
            self._domain_artifact_card(artifacts),
        ]

    def _aigc_status_card(self, task: Task, artifacts: list[Artifact]) -> dict[str, object]:
        prompt = self._domain_value(task, "prompt")
        variants = self._domain_value(task, "variants")
        selected_asset = self._domain_value(task, "selected_asset")
        review_notes = self._domain_value(task, "review_notes")
        model_settings = self._domain_value(task, "model_settings")
        review_status = self._domain_status(
            selected_asset,
            fallback=self._domain_value(task, "review_status"),
            default="pending_review" if _present(selected_asset) else "drafting",
        )
        metrics = {
            "prompt_present": _present(prompt),
            "variant_count": _count_items(variants),
            "selected_asset_present": _present(selected_asset),
            "model_settings_present": _present(model_settings),
            "review_note_count": _count_items(review_notes),
            "artifact_count": len(artifacts),
            "review_status": review_status,
        }
        recommended_actions = _aigc_recommended_actions(metrics)
        return {
            "card_type": "production_status",
            "title": "Production status",
            "status": self._domain_attention_status(recommended_actions),
            "data": {**metrics, "recommended_actions": recommended_actions},
        }

    def _novel_status_card(self, task: Task, artifacts: list[Artifact]) -> dict[str, object]:
        outline = self._domain_value(task, "outline")
        chapters = self._domain_value(task, "chapters")
        scenes = self._domain_value(task, "scenes")
        characters = self._domain_value(task, "characters")
        continuity_notes = self._domain_value(task, "continuity_notes")
        editorial_review = self._domain_value(task, "editorial_review")
        metrics = {
            "outline_item_count": _count_items(outline),
            "chapter_count": _count_items(chapters),
            "scene_count": _count_items(scenes),
            "character_count": _count_items(characters),
            "continuity_note_count": _count_items(continuity_notes),
            "word_count": _int_value(self._domain_value(task, "word_count")),
            "editorial_status": self._domain_status(
                editorial_review,
                fallback=self._domain_value(task, "editorial_status"),
                default="not_reviewed",
            ),
            "artifact_count": len(artifacts),
        }
        recommended_actions = _novel_recommended_actions(metrics)
        return {
            "card_type": "manuscript_status",
            "title": "Manuscript status",
            "status": self._domain_attention_status(recommended_actions),
            "data": {**metrics, "recommended_actions": recommended_actions},
        }

    def _research_status_card(self, task: Task, artifacts: list[Artifact]) -> dict[str, object]:
        confidence = self._domain_value(task, "confidence")
        metrics = {
            "source_count": _count_items(self._domain_value(task, "sources")),
            "claim_count": _count_items(self._domain_value(task, "claims")),
            "citation_count": _count_items(self._domain_value(task, "citations")),
            "report_section_count": _count_items(self._domain_value(task, "report_sections")),
            "confidence": confidence,
            "artifact_count": len(artifacts),
        }
        recommended_actions = _research_recommended_actions(metrics)
        return {
            "card_type": "research_status",
            "title": "Research status",
            "status": self._domain_attention_status(recommended_actions),
            "data": {**metrics, "recommended_actions": recommended_actions},
        }

    def _software_status_card(self, task: Task, artifacts: list[Artifact]) -> dict[str, object]:
        build_status = self._domain_value(task, "build_status")
        tests = self._domain_value(task, "tests")
        metrics = {
            "requirement_count": _count_items(self._domain_value(task, "requirements")),
            "design_task_count": _count_items(self._domain_value(task, "design_tasks")),
            "branch_count": _count_items(self._domain_value(task, "branches")),
            "patch_count": _count_items(self._domain_value(task, "patches")),
            "test_count": _count_items(tests),
            "build_status": self._domain_status(build_status, default="not_run"),
            "review_comment_count": _count_items(self._domain_value(task, "review_comments")),
            "artifact_count": len(artifacts),
        }
        recommended_actions = _software_recommended_actions(metrics)
        return {
            "card_type": "delivery_status",
            "title": "Delivery status",
            "status": self._domain_attention_status(recommended_actions),
            "data": {**metrics, "recommended_actions": recommended_actions},
        }

    def _domain_card(
        self,
        key: str,
        title: str,
        domain_state: dict[str, object],
    ) -> dict[str, object]:
        value = domain_state.get(key)
        return {
            "card_type": key,
            "title": title,
            "status": "available" if value not in (None, "", [], {}) else "empty",
            "data": {"value": value},
        }

    def _domain_value(self, task: Task, key: str) -> object:
        if isinstance(task.domain_state, dict) and key in task.domain_state:
            return task.domain_state[key]
        return None

    def _domain_status(
        self,
        value: object,
        *,
        fallback: object = None,
        default: str,
    ) -> str:
        for candidate in (value, fallback):
            if isinstance(candidate, str) and candidate:
                return candidate
            if isinstance(candidate, dict):
                raw_status = candidate.get("status") or candidate.get("review_status")
                if isinstance(raw_status, str) and raw_status:
                    return raw_status
        return default

    def _domain_attention_status(self, recommended_actions: list[str]) -> str:
        return "attention" if recommended_actions else "healthy"

    def _domain_artifact_card(self, artifacts: list[Artifact]) -> dict[str, object]:
        return {
            "card_type": "domain_artifacts",
            "title": "Related artifacts",
            "status": "available" if artifacts else "empty",
            "data": {
                "items": [
                    {
                        "id": str(artifact.id),
                        "filename": artifact.filename,
                        "artifact_type": artifact.artifact_type,
                        "content_type": artifact.content_type,
                    }
                    for artifact in artifacts
                ]
            },
        }

    def _summary(
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

    def _step_card(
        self,
        step: TaskStep,
        agents: dict[UUID, AgentProfile],
    ) -> dict[str, object]:
        agent = (
            agents.get(step.assigned_agent_profile_id)
            if step.assigned_agent_profile_id
            else None
        )
        return {
            "card_type": "task_step",
            "title": step.title,
            "status": step.status,
            "data": redact_sensitive_payload(
                {
                    "id": str(step.id),
                    "order_index": step.order_index,
                    "work_package_id": step.work_package_id,
                    "required_role": step.required_role,
                    "required_skills": step.required_skills,
                    "expected_artifacts": step.expected_artifacts,
                    "acceptance_criteria": step.acceptance_criteria,
                    "review_policy": step.review_policy,
                    "dependencies": step.dependencies,
                    "result_summary": step.result_summary,
                    "agent": self._agent_payload(agent) if agent is not None else None,
                }
            ),
        }

    def _agent_payload(self, agent: AgentProfile) -> dict[str, object]:
        return {"id": str(agent.id), "name": agent.name, "role": agent.role, "status": agent.status}

    def _safe_message_payload(self, payload: dict[str, object]) -> dict[str, object]:
        return redact_sensitive_payload(payload)

    def _list_steps(self, workspace_id: UUID, task_id: UUID) -> list[TaskStep]:
        return list(
            self._session.scalars(
                select(TaskStep)
                .where(TaskStep.workspace_id == workspace_id, TaskStep.task_id == task_id)
                .order_by(TaskStep.order_index.asc(), TaskStep.created_at.asc())
            )
        )

    def _list_runs(self, workspace_id: UUID, task_id: UUID) -> list[AgentRun]:
        return list(
            self._session.scalars(
                select(AgentRun)
                .where(AgentRun.workspace_id == workspace_id, AgentRun.task_id == task_id)
                .order_by(AgentRun.created_at.asc())
            )
        )

    def _list_messages(self, workspace_id: UUID, task_id: UUID) -> list[TaskMessage]:
        return list(
            self._session.scalars(
                select(TaskMessage)
                .where(TaskMessage.workspace_id == workspace_id, TaskMessage.task_id == task_id)
                .order_by(TaskMessage.sequence.asc())
            )
        )

    def _list_artifacts(self, workspace_id: UUID, task_id: UUID) -> list[Artifact]:
        return list(
            self._session.scalars(
                select(Artifact)
                .where(Artifact.workspace_id == workspace_id, Artifact.task_id == task_id)
                .order_by(Artifact.created_at.asc())
            )
        )

    def _list_run_events(self, workspace_id: UUID, runs: list[AgentRun]) -> list[RunEvent]:
        run_ids = [run.id for run in runs]
        if not run_ids:
            return []
        return list(
            self._session.scalars(
                select(RunEvent)
                .where(RunEvent.workspace_id == workspace_id, RunEvent.agent_run_id.in_(run_ids))
                .order_by(RunEvent.created_at.asc(), RunEvent.sequence.asc())
            )
        )

    def _agent_map(
        self,
        workspace_id: UUID,
        steps: list[TaskStep],
        runs: list[AgentRun],
        messages: list[TaskMessage],
    ) -> dict[UUID, AgentProfile]:
        agent_ids = {
            item
            for item in (
                [step.assigned_agent_profile_id for step in steps]
                + [run.agent_profile_id for run in runs]
                + [message.agent_profile_id for message in messages]
            )
            if item is not None
        }
        if not agent_ids:
            return {}
        agents = self._session.scalars(
            select(AgentProfile).where(
                AgentProfile.workspace_id == workspace_id,
                AgentProfile.id.in_(agent_ids),
            )
        ).all()
        return {agent.id: agent for agent in agents}

    def _status_from_message_type(self, message_type: str) -> str:
        if message_type.endswith(".failed") or message_type.endswith(".blocked"):
            return "attention"
        if message_type.endswith(".completed") or message_type == "pm.acceptance_decision":
            return "completed"
        return "recorded"

    def _str_or_none(self, value: Any) -> str | None:
        return str(value) if value is not None else None


def _risk_flags_from_payload(payload: dict[str, object]) -> list[dict[str, object]]:
    risks: list[dict[str, object]] = []
    raw_risks = payload.get("risks")
    if isinstance(raw_risks, list):
        risks.extend(item for item in raw_risks if isinstance(item, dict))

    risk_level = payload.get("risk_level")
    if isinstance(risk_level, str) and risk_level:
        risks.append(
            {
                "title": str(payload.get("title") or "Risk flagged"),
                "severity": risk_level,
                "reason": payload.get("reason") or payload.get("summary"),
            }
        )

    decision = payload.get("decision")
    reasons = payload.get("reasons")
    if decision == "request_revision":
        risks.append(
            {
                "title": "Revision requested",
                "severity": "attention",
                "reason": reasons if isinstance(reasons, list) else payload.get("summary"),
            }
        )
    return risks


def _count_items(value: object) -> int:
    if value in (None, "", [], {}):
        return 0
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if isinstance(value, dict):
        for key in ("items", "results", "records", "entries"):
            nested = value.get(key)
            if isinstance(nested, (list, tuple, set)):
                return len(nested)
        count = value.get("count")
        if isinstance(count, int):
            return max(count, 0)
        return len(value)
    if isinstance(value, str):
        return 1 if value.strip() else 0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return 1 if value > 0 else 0
    return 1 if value else 0


def _present(value: object) -> bool:
    return _count_items(value) > 0


def _int_value(value: object) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    if isinstance(value, str):
        try:
            return max(int(value.strip()), 0)
        except ValueError:
            return 0
    if isinstance(value, dict):
        for key in ("count", "value", "total"):
            parsed = _int_value(value.get(key))
            if parsed:
                return parsed
    return 0


def _aigc_recommended_actions(metrics: dict[str, object]) -> list[str]:
    actions: list[str] = []
    if not metrics["prompt_present"]:
        actions.append("add_prompt")
    if metrics["variant_count"] == 0:
        actions.append("generate_variants")
    if not metrics["selected_asset_present"]:
        actions.append("select_asset")
    if metrics["selected_asset_present"] and metrics["review_status"] not in {
        "approved",
        "accepted",
    }:
        actions.append("request_review")
    if metrics["artifact_count"] == 0:
        actions.append("persist_asset_artifact")
    return actions

def _novel_recommended_actions(metrics: dict[str, object]) -> list[str]:
    actions: list[str] = []
    if metrics["outline_item_count"] == 0:
        actions.append("create_outline")
    if metrics["chapter_count"] == 0:
        actions.append("draft_chapters")
    if metrics["character_count"] == 0:
        actions.append("define_characters")
    if metrics["word_count"] == 0 and metrics["chapter_count"] > 0:
        actions.append("update_word_count")
    if metrics["chapter_count"] > 0 and metrics["continuity_note_count"] == 0:
        actions.append("check_continuity")
    if metrics["chapter_count"] > 0 and metrics["editorial_status"] not in {
        "approved",
        "accepted",
    }:
        actions.append("request_editorial_review")
    return actions


def _research_recommended_actions(metrics: dict[str, object]) -> list[str]:
    actions: list[str] = []
    if metrics["source_count"] == 0:
        actions.append("collect_sources")
    if metrics["claim_count"] == 0:
        actions.append("extract_claims")
    if metrics["citation_count"] == 0 and metrics["claim_count"] > 0:
        actions.append("add_citations")
    if metrics["report_section_count"] == 0:
        actions.append("draft_report_sections")
    if metrics["artifact_count"] == 0 and metrics["report_section_count"] > 0:
        actions.append("export_research_artifact")
    return actions


def _software_recommended_actions(metrics: dict[str, object]) -> list[str]:
    actions: list[str] = []
    if metrics["requirement_count"] == 0:
        actions.append("capture_requirements")
    if metrics["design_task_count"] == 0:
        actions.append("create_design_tasks")
    if metrics["patch_count"] == 0:
        actions.append("produce_patch")
    if metrics["test_count"] == 0:
        actions.append("run_tests")
    if metrics["build_status"] not in {"passed", "success", "green"}:
        actions.append("run_build")
    if metrics["review_comment_count"] > 0:
        actions.append("resolve_review_comments")
    return actions
