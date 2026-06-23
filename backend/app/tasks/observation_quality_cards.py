from backend.app.security.redaction import redact_sensitive_payload
from backend.app.tasks.models import TaskMessage, TaskStep
from backend.app.tasks.observation_utils import risk_flags_from_payload, safe_message_payload


class TaskObservationQualityCards:
    def build(
        self,
        steps: list[TaskStep],
        messages: list[TaskMessage],
    ) -> list[dict[str, object]]:
        return [
            *self.revision_history_cards(steps, messages),
            *self.risk_flag_cards(steps, messages),
        ]

    def revision_history_cards(
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

    def risk_flag_cards(
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
            payload = safe_message_payload(message.payload)
            risks = risk_flags_from_payload(payload)
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
