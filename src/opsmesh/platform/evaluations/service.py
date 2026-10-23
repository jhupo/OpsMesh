"""Owned dataset releases and experiments with stable per-case run identities."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.platform.audit.contracts import AuditActor, AuditRecord, record_audit
from opsmesh.platform.evaluations.contracts import DatasetSpec, EvaluatorRegistry, ExperimentSpec
from opsmesh.platform.evaluations.models import (
    DatasetVersion,
    EvaluationCase,
    EvaluationDataset,
    EvaluationExperiment,
)
from opsmesh.platform.targets import TargetPorts
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.users.access import CurrentUser


def audit(session: Session, actor: CurrentUser, action: str, identifier: UUID) -> None:
    record_audit(
        session,
        AuditRecord(
            category="evaluations",
            action=action,
            actor=AuditActor.from_user(actor),
            source="platform",
            target_type="evaluation",
            target_id=str(identifier),
        ),
    )


def owned_dataset(
    session: Session, actor: CurrentUser, identifier: UUID, *, lock: bool = False
) -> EvaluationDataset:
    row = session.get(EvaluationDataset, identifier, with_for_update=lock)
    if row is None or row.owner_user_id != actor.user_id:
        raise NotFoundError("The dataset does not exist")
    return row


def owned_experiment(
    session: Session, actor: CurrentUser, identifier: UUID, *, lock: bool = False
) -> EvaluationExperiment:
    row = session.get(EvaluationExperiment, identifier, with_for_update=lock)
    if row is None or row.owner_user_id != actor.user_id:
        raise NotFoundError("The experiment does not exist")
    return row


def dataset_spec(
    session: Session, cipher: SecretCipher, dataset_id: UUID, number: int
) -> DatasetSpec:
    version = session.get(DatasetVersion, (dataset_id, number))
    if version is None:
        raise NotFoundError("The dataset version does not exist")
    return DatasetSpec.model_validate(
        cipher.decrypt_payload(version.encrypted_spec, key_id=version.key_id)
    )


def save_dataset(
    session: Session,
    *,
    actor: CurrentUser,
    cipher: SecretCipher,
    spec: DatasetSpec,
    expected_version: int,
    dataset_id: UUID | None = None,
) -> EvaluationDataset:
    row = owned_dataset(session, actor, dataset_id, lock=True) if dataset_id else None
    if expected_version != (row.version if row else 0):
        raise ConflictError("The dataset version changed")
    payload = spec.model_dump(mode="json")
    if len(canonical_json(payload).encode()) > 1_000_000:
        raise InvalidArgumentError("The dataset exceeds its size limit")
    if row is None:
        row = EvaluationDataset(owner_user_id=actor.user_id, name=spec.name, version=1)
        session.add(row)
        session.flush()
    else:
        row.name, row.version = spec.name, row.version + 1
    encrypted = cipher.encrypt_payload(payload)
    session.add(
        DatasetVersion(
            dataset_id=row.id,
            number=row.version,
            digest=payload_digest(payload),
            encrypted_spec=encrypted.ciphertext,
            key_id=encrypted.key_id,
        )
    )
    session.flush()
    audit(session, actor, "dataset.publish", row.id)
    return row


def create_experiment(
    session: Session,
    *,
    actor: CurrentUser,
    cipher: SecretCipher,
    spec: ExperimentSpec,
    ports: TargetPorts,
    evaluators: EvaluatorRegistry,
) -> EvaluationExperiment:
    owned_dataset(session, actor, spec.dataset_id)
    dataset = dataset_spec(session, cipher, spec.dataset_id, spec.dataset_version)
    for target in spec.targets:
        ports.authorize(session, actor, target.kind, target.instance_id)
    for evaluator in spec.evaluators:
        try:
            evaluators.get(evaluator)
        except ValueError as exc:
            raise InvalidArgumentError(str(exc)) from exc
    payload = spec.model_dump(mode="json")
    fingerprint = payload_digest(payload)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"evaluation:{actor.user_id}:{spec.request_key}"},
    )
    previous = session.scalar(
        select(EvaluationExperiment).where(
            EvaluationExperiment.owner_user_id == actor.user_id,
            EvaluationExperiment.request_key == spec.request_key,
        )
    )
    if previous is not None:
        if previous.fingerprint != fingerprint:
            raise ConflictError("The experiment request identity was reused")
        return previous
    encrypted = cipher.encrypt_payload(payload)
    row = EvaluationExperiment(
        owner_user_id=actor.user_id,
        dataset_id=spec.dataset_id,
        dataset_version=spec.dataset_version,
        request_key=spec.request_key,
        fingerprint=fingerprint,
        encrypted_spec=encrypted.ciphertext,
        key_id=encrypted.key_id,
        status="queued",
    )
    session.add(row)
    session.flush()
    for case in dataset.cases:
        for target in spec.targets:
            session.add(
                EvaluationCase(
                    experiment_id=row.id,
                    case_id=case.id,
                    target_id=target.instance_id,
                    target_kind=target.kind,
                    status="pending",
                )
            )
    session.flush()
    audit(session, actor, "experiment.create", row.id)
    return row
