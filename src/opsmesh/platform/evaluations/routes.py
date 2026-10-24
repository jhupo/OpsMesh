"""Dataset versions, runnable experiments, comparisons and owned run feedback."""

from collections.abc import Callable
from typing import Annotated, Literal, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.platform.dependencies import ports
from opsmesh.platform.evaluations import service
from opsmesh.platform.evaluations.contracts import DatasetSpec, EvaluatorRegistry, ExperimentSpec
from opsmesh.platform.evaluations.models import (
    DatasetVersion,
    EvaluationCase,
    EvaluationDataset,
    EvaluationExperiment,
    RunFeedback,
)
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.http.dependencies import secret_cipher
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession

router = APIRouter(prefix="/evaluations", tags=["evaluations"])


class DatasetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    spec: DatasetSpec


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_kind: Literal["agent", "workflow"]
    run_id: UUID
    rating: int = Field(ge=-1, le=1)
    comment: str = Field(default="", max_length=4000)
    expected: JsonValue = None


def registry(request: Request) -> EvaluatorRegistry:
    value = getattr(request.app.state, "evaluation_evaluators", None)
    if not isinstance(value, EvaluatorRegistry):
        raise HTTPException(503, "Evaluation adapters are not configured")
    return value


def experiment_view(row: EvaluationExperiment) -> dict[str, object]:
    return {
        "id": row.id,
        "dataset_id": row.dataset_id,
        "dataset_version": row.dataset_version,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.post("/datasets", status_code=201, operation_id="evaluations.datasets.create")
def create_dataset(
    payload: DatasetRequest, session: DbSession, user: CurrentUserDep, request: Request
) -> dict[str, object]:
    with session_transaction(session):
        row = service.save_dataset(
            session,
            actor=user,
            cipher=secret_cipher(request),
            spec=payload.spec,
            expected_version=payload.expected_version,
        )
        return {"id": row.id, "name": row.name, "version": row.version}


@router.put("/datasets/{dataset_id}", operation_id="evaluations.datasets.publish")
def update_dataset(
    dataset_id: UUID,
    payload: DatasetRequest,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict[str, object]:
    with session_transaction(session):
        row = service.save_dataset(
            session,
            actor=user,
            cipher=secret_cipher(request),
            spec=payload.spec,
            expected_version=payload.expected_version,
            dataset_id=dataset_id,
        )
        return {"id": row.id, "name": row.name, "version": row.version}


@router.get("/datasets", operation_id="evaluations.datasets.list")
def datasets(
    session: DbSession,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    return [
        {"id": r.id, "name": r.name, "version": r.version, "updated_at": r.updated_at}
        for r in session.scalars(
            select(EvaluationDataset)
            .where(EvaluationDataset.owner_user_id == user.user_id)
            .order_by(EvaluationDataset.created_at.desc(), EvaluationDataset.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.get("/datasets/{dataset_id}/versions/{number}", operation_id="evaluations.datasets.version")
def dataset_version(
    dataset_id: UUID, number: int, session: DbSession, user: CurrentUserDep, request: Request
) -> DatasetSpec:
    service.owned_dataset(session, user, dataset_id)
    return service.dataset_spec(session, secret_cipher(request), dataset_id, number)


@router.get("/datasets/{dataset_id}/versions", operation_id="evaluations.datasets.versions")
def dataset_versions(
    dataset_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> list[dict[str, object]]:
    service.owned_dataset(session, user, dataset_id)
    query = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    if before is not None:
        query = query.where(DatasetVersion.number < before)
    return [
        {"number": v.number, "digest": v.digest, "created_at": v.created_at}
        for v in session.scalars(query.order_by(DatasetVersion.number.desc()).limit(limit))
    ]


@router.post("/experiments", status_code=202, operation_id="evaluations.experiments.create")
def create_experiment(
    payload: ExperimentSpec, session: DbSession, user: CurrentUserDep, request: Request
) -> dict[str, object]:
    dispatch = getattr(request.app.state, "evaluation_dispatch", None)
    if not callable(dispatch):
        raise HTTPException(503, "Evaluation dispatch is not configured")
    with session_transaction(session):
        row = service.create_experiment(
            session,
            actor=user,
            cipher=secret_cipher(request),
            spec=payload,
            ports=ports(request),
            evaluators=registry(request),
        )
        cast(Callable[[Session, UUID], None], dispatch)(session, row.id)
        return experiment_view(row)


@router.get("/experiments", operation_id="evaluations.experiments.list")
def experiments(
    session: DbSession,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    return [
        experiment_view(r)
        for r in session.scalars(
            select(EvaluationExperiment)
            .where(EvaluationExperiment.owner_user_id == user.user_id)
            .order_by(EvaluationExperiment.created_at.desc(), EvaluationExperiment.id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.get("/experiments/{experiment_id}", operation_id="evaluations.experiments.read")
def experiment(
    experiment_id: UUID, session: DbSession, user: CurrentUserDep, request: Request
) -> dict[str, object]:
    row = service.owned_experiment(session, user, experiment_id)
    return {
        **experiment_view(row),
        "spec": secret_cipher(request).decrypt_payload(row.encrypted_spec, key_id=row.key_id),
    }


@router.post("/experiments/{experiment_id}/cancel", operation_id="evaluations.experiments.cancel")
def cancel(experiment_id: UUID, session: DbSession, user: CurrentUserDep) -> dict[str, object]:
    with session_transaction(session):
        row = service.owned_experiment(session, user, experiment_id, lock=True)
        if row.status not in {"completed", "cancelled", "failed"}:
            row.status = "cancelling"
            service.audit(session, user, "experiment.cancel", row.id)
        return experiment_view(row)


@router.get("/experiments/{experiment_id}/cases", operation_id="evaluations.experiments.cases")
def cases(
    experiment_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    service.owned_experiment(session, user, experiment_id)
    cipher = secret_cipher(request)
    return [
        {
            "id": r.id,
            "case_id": r.case_id,
            "target_id": r.target_id,
            "run_id": r.run_id,
            "status": r.status,
            "error_code": r.error_code,
            "result": cipher.decrypt_payload(r.encrypted_result, key_id=r.key_id)
            if r.encrypted_result
            else None,
        }
        for r in session.scalars(
            select(EvaluationCase)
            .where(EvaluationCase.experiment_id == experiment_id)
            .order_by(EvaluationCase.case_id, EvaluationCase.target_id)
            .limit(limit)
            .offset(offset)
        )
    ]


@router.get("/compare", operation_id="evaluations.compare")
def compare(
    left: UUID, right: UUID, session: DbSession, user: CurrentUserDep, request: Request
) -> dict[str, object]:
    cipher = secret_cipher(request)
    a, b = (
        service.owned_experiment(session, user, left),
        service.owned_experiment(session, user, right),
    )
    specs = [
        ExperimentSpec.model_validate(cipher.decrypt_payload(r.encrypted_spec, key_id=r.key_id))
        for r in (a, b)
    ]

    def aggregate(row: EvaluationExperiment) -> dict[str, object]:
        buckets: dict[str, list[float]] = {}
        statuses: dict[str, int] = {}
        for case in session.scalars(
            select(EvaluationCase).where(EvaluationCase.experiment_id == row.id)
        ):
            statuses[case.status] = statuses.get(case.status, 0) + 1
            if not case.encrypted_result:
                continue
            payload = cipher.decrypt_payload(case.encrypted_result, key_id=case.key_id)
            scores = payload.get("scores")
            if isinstance(scores, dict):
                for name, score in scores.items():
                    if isinstance(score, dict) and isinstance(score.get("value"), (int, float)):
                        key = f"{case.target_kind}:{case.target_id}:{name}"
                        buckets.setdefault(key, []).append(float(score["value"]))
        return {
            **experiment_view(row),
            "case_statuses": statuses,
            "total_cases": sum(statuses.values()),
            "success_rate": statuses.get("completed", 0) / sum(statuses.values())
            if statuses
            else None,
            "score_basis": "completed_and_scored_cases_only",
            "scores": {
                key: {"mean": sum(v) / len(v), "count": len(v)} for key, v in buckets.items()
            },
        }

    return {
        "comparable": a.dataset_id == b.dataset_id
        and a.dataset_version == b.dataset_version
        and specs[0].evaluators == specs[1].evaluators,
        "left": aggregate(a),
        "right": aggregate(b),
    }


@router.put("/feedback", operation_id="evaluations.feedback.save")
def feedback(
    payload: FeedbackRequest, session: DbSession, user: CurrentUserDep, request: Request
) -> dict[str, object]:
    with session_transaction(session):
        ports(request).result(session, user, payload.target_kind, payload.run_id)
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"feedback:{user.user_id}:{payload.target_kind}:{payload.run_id}"},
        )
        row = session.scalar(
            select(RunFeedback).where(
                RunFeedback.owner_user_id == user.user_id,
                RunFeedback.target_kind == payload.target_kind,
                RunFeedback.run_id == payload.run_id,
            )
        )
        if row is None:
            row = RunFeedback(
                owner_user_id=user.user_id, target_kind=payload.target_kind, run_id=payload.run_id
            )
            session.add(row)
        encrypted = secret_cipher(request).encrypt_payload(payload.model_dump(mode="json"))
        row.rating, row.encrypted_payload, row.key_id = (
            payload.rating,
            encrypted.ciphertext,
            encrypted.key_id,
        )
        session.flush()
        service.audit(session, user, "feedback.save", row.id)
        return {"id": row.id, "rating": row.rating}


@router.get("/feedback", operation_id="evaluations.feedback.read")
def read_feedback(
    target_kind: Literal["agent", "workflow"],
    run_id: UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> dict[str, object]:
    row = session.scalar(
        select(RunFeedback).where(
            RunFeedback.owner_user_id == user.user_id,
            RunFeedback.target_kind == target_kind,
            RunFeedback.run_id == run_id,
        )
    )
    if row is None:
        raise NotFoundError("The feedback does not exist")
    ports(request).result(session, user, target_kind, run_id)
    return {
        "id": row.id,
        **secret_cipher(request).decrypt_payload(row.encrypted_payload, key_id=row.key_id),
    }
