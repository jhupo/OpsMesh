"""Evaluation Activities delegate to ordinary execution services and evaluator adapters."""

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from opsmesh.platform.evaluations import service
from opsmesh.platform.evaluations.contracts import EvaluatorRegistry, ExperimentSpec
from opsmesh.platform.evaluations.models import EvaluationCase, EvaluationExperiment
from opsmesh.platform.targets import TargetPorts
from opsmesh.shared.errors import DomainError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.users.service import current_user


class EvaluationActivities:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        cipher: SecretCipher,
        ports: TargetPorts,
        evaluators: EvaluatorRegistry,
    ) -> None:
        self.sessions, self.cipher, self.ports, self.evaluators = (
            sessions,
            cipher,
            ports,
            evaluators,
        )

    def spec(self, row: EvaluationExperiment) -> ExperimentSpec:
        return ExperimentSpec.model_validate(
            self.cipher.decrypt_payload(row.encrypted_spec, key_id=row.key_id)
        )

    @activity.defn(name="evaluation.plan")
    async def plan(self, identifier: str) -> dict[str, object]:
        def read() -> dict[str, object]:
            with self.sessions.begin() as session:
                row = session.get(EvaluationExperiment, UUID(identifier), with_for_update=True)
                if row is None:
                    raise ApplicationError("The experiment does not exist", non_retryable=True)
                if row.status in {"completed", "cancelled", "failed"}:
                    return {"status": row.status, "cases": []}
                spec = self.spec(row)
                unfinished = list(
                    session.scalars(
                        select(EvaluationCase)
                        .where(
                            EvaluationCase.experiment_id == row.id,
                            EvaluationCase.status.in_(("pending", "running", "needs_attention")),
                        )
                        .order_by((EvaluationCase.status == "pending"), EvaluationCase.id)
                        .limit(spec.concurrency)
                    )
                )
                if not unfinished:
                    row.status = "cancelled" if row.status == "cancelling" else "completed"
                elif row.status != "cancelling":
                    row.status = (
                        "needs_attention"
                        if any(c.status == "needs_attention" for c in unfinished)
                        else "running"
                    )
                return {"status": row.status, "cases": [str(c.id) for c in unfinished]}

        return await asyncio.to_thread(read)

    @activity.defn(name="evaluation.step")
    async def step(self, identifier: str) -> None:
        def apply() -> None:
            with self.sessions.begin() as session:
                initial = session.get(EvaluationCase, UUID(identifier))
                if initial is None:
                    raise ApplicationError("The evaluation case does not exist", non_retryable=True)
                experiment = session.get(
                    EvaluationExperiment, initial.experiment_id, with_for_update=True
                )
                assert experiment is not None
                row = session.get(
                    EvaluationCase, initial.id, with_for_update=True, populate_existing=True
                )
                assert row is not None
                if row.status not in {"pending", "running", "needs_attention"}:
                    return
                actor = current_user(session, experiment.owner_user_id)
                if row.run_id is None and experiment.status == "cancelling":
                    row.status = "cancelled"
                    return
                spec = self.spec(experiment)
                dataset = service.dataset_spec(
                    session, self.cipher, spec.dataset_id, spec.dataset_version
                )
                case = next(c for c in dataset.cases if c.id == row.case_id)
                if row.run_id is None:
                    row.run_id = self.ports.submit(
                        session,
                        actor,
                        row.target_kind,
                        row.target_id,
                        case.input,
                        f"evaluation:{row.id}",
                    )
                    row.status = "running"
                    return
                result = self.ports.result(session, actor, row.target_kind, row.run_id)
                status = result["status"]
                if experiment.status == "cancelling" and status not in {
                    "completed",
                    "cancelled",
                    "failed",
                }:
                    self.ports.cancel(session, actor, row.target_kind, row.run_id)
                if status in {"needs_attention", "interrupted"}:
                    row.status = "needs_attention"
                    return
                if status not in {"completed", "failed", "cancelled"}:
                    return
                scores: dict[str, object] = {}
                if status == "completed":
                    for evaluator in spec.evaluators:
                        try:
                            score = self.evaluators.get(evaluator)(
                                case, result.get("output"), evaluator.config
                            )
                            scores[evaluator.name] = score.model_dump(mode="json")
                        except Exception as exc:
                            row.status, row.error_code = (
                                "failed",
                                "evaluator_" + type(exc).__name__[:80],
                            )
                            return
                encrypted = self.cipher.encrypt_payload({"run": result, "scores": scores})
                row.encrypted_result, row.key_id = encrypted.ciphertext, encrypted.key_id
                row.status = str(status)
                error = result.get("error_code")
                row.error_code = str(error)[:100] if error is not None else None

        try:
            await asyncio.to_thread(apply)
        except DomainError as error:
            code = error.code[:100]

            # A denied or invalid case must not leave the whole experiment permanently running.
            def failed() -> None:
                with self.sessions.begin() as session:
                    row = session.get(EvaluationCase, UUID(identifier), with_for_update=True)
                    if row is not None:
                        row.status = "failed" if row.run_id is None else "needs_attention"
                        row.error_code = code

            await asyncio.to_thread(failed)
