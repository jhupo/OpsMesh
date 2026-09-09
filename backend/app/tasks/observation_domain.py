from __future__ import annotations

from typing import TypedDict

from backend.app.artifacts.models import Artifact
from backend.app.tasks.models import Task
from backend.app.tasks.observation_utils import count_items, int_value, present


class AigcMetrics(TypedDict):
    prompt_present: bool
    variant_count: int
    selected_asset_present: bool
    model_settings_present: bool
    review_note_count: int
    artifact_count: int
    review_status: str


class NovelMetrics(TypedDict):
    outline_item_count: int
    chapter_count: int
    scene_count: int
    character_count: int
    continuity_note_count: int
    word_count: int
    editorial_status: str
    artifact_count: int


class ResearchMetrics(TypedDict):
    source_count: int
    claim_count: int
    citation_count: int
    report_section_count: int
    confidence: object
    artifact_count: int


class SoftwareMetrics(TypedDict):
    requirement_count: int
    design_task_count: int
    branch_count: int
    patch_count: int
    test_count: int
    build_status: str
    review_comment_count: int
    artifact_count: int


class TaskObservationDomainCards:
    def domain_section(
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
            default="pending_review" if present(selected_asset) else "drafting",
        )
        metrics: AigcMetrics = {
            "prompt_present": present(prompt),
            "variant_count": count_items(variants),
            "selected_asset_present": present(selected_asset),
            "model_settings_present": present(model_settings),
            "review_note_count": count_items(review_notes),
            "artifact_count": len(artifacts),
            "review_status": review_status,
        }
        recommended_actions = _aigc_recommended_actions(metrics)
        return {
            "card_type": "production_status",
            "title": "Production status",
            "status": _domain_attention_status(recommended_actions),
            "data": {**metrics, "recommended_actions": recommended_actions},
        }

    def _novel_status_card(self, task: Task, artifacts: list[Artifact]) -> dict[str, object]:
        outline = self._domain_value(task, "outline")
        chapters = self._domain_value(task, "chapters")
        scenes = self._domain_value(task, "scenes")
        characters = self._domain_value(task, "characters")
        continuity_notes = self._domain_value(task, "continuity_notes")
        editorial_review = self._domain_value(task, "editorial_review")
        metrics: NovelMetrics = {
            "outline_item_count": count_items(outline),
            "chapter_count": count_items(chapters),
            "scene_count": count_items(scenes),
            "character_count": count_items(characters),
            "continuity_note_count": count_items(continuity_notes),
            "word_count": int_value(self._domain_value(task, "word_count")),
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
            "status": _domain_attention_status(recommended_actions),
            "data": {**metrics, "recommended_actions": recommended_actions},
        }

    def _research_status_card(self, task: Task, artifacts: list[Artifact]) -> dict[str, object]:
        confidence = self._domain_value(task, "confidence")
        metrics: ResearchMetrics = {
            "source_count": count_items(self._domain_value(task, "sources")),
            "claim_count": count_items(self._domain_value(task, "claims")),
            "citation_count": count_items(self._domain_value(task, "citations")),
            "report_section_count": count_items(self._domain_value(task, "report_sections")),
            "confidence": confidence,
            "artifact_count": len(artifacts),
        }
        recommended_actions = _research_recommended_actions(metrics)
        return {
            "card_type": "research_status",
            "title": "Research status",
            "status": _domain_attention_status(recommended_actions),
            "data": {**metrics, "recommended_actions": recommended_actions},
        }

    def _software_status_card(self, task: Task, artifacts: list[Artifact]) -> dict[str, object]:
        build_status = self._domain_value(task, "build_status")
        tests = self._domain_value(task, "tests")
        metrics: SoftwareMetrics = {
            "requirement_count": count_items(self._domain_value(task, "requirements")),
            "design_task_count": count_items(self._domain_value(task, "design_tasks")),
            "branch_count": count_items(self._domain_value(task, "branches")),
            "patch_count": count_items(self._domain_value(task, "patches")),
            "test_count": count_items(tests),
            "build_status": self._domain_status(build_status, default="not_run"),
            "review_comment_count": count_items(self._domain_value(task, "review_comments")),
            "artifact_count": len(artifacts),
        }
        recommended_actions = _software_recommended_actions(metrics)
        return {
            "card_type": "delivery_status",
            "title": "Delivery status",
            "status": _domain_attention_status(recommended_actions),
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


def _domain_attention_status(recommended_actions: list[str]) -> str:
    return "attention" if recommended_actions else "healthy"


def _aigc_recommended_actions(metrics: AigcMetrics) -> list[str]:
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


def _novel_recommended_actions(metrics: NovelMetrics) -> list[str]:
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


def _research_recommended_actions(metrics: ResearchMetrics) -> list[str]:
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


def _software_recommended_actions(metrics: SoftwareMetrics) -> list[str]:
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
