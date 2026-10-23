"""Transactional run admission and completion; no scheduling framework imports."""

from uuid import UUID, uuid4

from opsmesh_sdk.identity import InvocationSource, Principal
from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from opsmesh.execution.context import ExecutionContext
from opsmesh.execution.contracts import ExecutionIdentity, PreparedAgent, SessionReference
from opsmesh.execution.instances.access import get_instance
from opsmesh.execution.preparation import AgentSnapshot
from opsmesh.execution.runs.access import authorize_run as authorize_run
from opsmesh.execution.runs.access import get_run
from opsmesh.execution.runs.events import append_event
from opsmesh.execution.runs.models import ACTIVE, ExecutionOperation, ExecutionRun
from opsmesh.execution.runs.schemas import RunCreate
from opsmesh.execution.runtime.access import get_environment
from opsmesh.execution.runtime.models import SandboxEnvironment
from opsmesh.execution.sessions.service import get_session
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.users.access import CurrentUser
from opsmesh.users.quotas import service as quotas
from opsmesh.users.service import current_user


class AdmissionPending(ConflictError):
    """The queued run is valid but a preceding run still owns its resources."""


def submit_run(
    session: Session,
    *,
    actor: CurrentUser,
    request: RunCreate,
    cipher: SecretCipher,
    source: InvocationSource | None = None,
) -> ExecutionRun:
    current_user(session, actor.user_id)
    payload = request.model_dump(mode="json")
    if len(canonical_json(payload).encode()) > 1_000_000:
        raise InvalidArgumentError("The execution request exceeds the size limit")
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"run:{actor.user_id}:{request.request_key}"},
    )
    fingerprint = payload_digest(
        payload if source is None else {**payload, "source": source.model_dump(mode="json")}
    )
    previous = session.scalar(
        select(ExecutionRun).where(
            ExecutionRun.user_id == actor.user_id, ExecutionRun.request_key == request.request_key
        )
    )
    if previous is not None:
        if previous.fingerprint != fingerprint:
            raise ConflictError("The run request key was reused with different input")
        return previous
    instance = get_instance(session, actor=actor, instance_id=request.instance_id, lock=True)
    conversation = None
    if request.session_id is not None:
        conversation = get_session(session, actor=actor, session_id=request.session_id, lock=True)
        if conversation.instance_id != instance.id or conversation.status != "active":
            raise InvalidArgumentError("The conversation must belong to this Agent instance")
    snapshot = AgentSnapshot.model_validate(cipher.decrypt_payload(instance.encrypted_config))
    if snapshot.requires_sandbox != (instance.environment_id is not None):
        raise ConflictError("The stored instance environment does not match its configuration")
    if instance.environment_id is not None:
        environment = get_environment(session, actor=actor, environment_id=instance.environment_id)
        if environment.status != "ready":
            raise ConflictError("The persistent environment is not ready")
    run_id = uuid4()
    prepared = PreparedAgent(
        identity=ExecutionIdentity(run_id=run_id, user_id=actor.user_id),
        instance_id=instance.id,
        snapshot=snapshot,
        session=SessionReference(session_id=conversation.id, generation=conversation.generation)
        if conversation
        else None,
        environment_id=instance.environment_id,
        max_turns=request.max_turns,
        source=source or InvocationSource(),
    )
    encrypted = cipher.encrypt_payload(payload)
    row = ExecutionRun(
        id=run_id,
        user_id=actor.user_id,
        kind="agent",
        instance_id=instance.id,
        driver=request.driver,
        session_id=request.session_id,
        sequence=conversation.next_sequence if conversation else None,
        generation=conversation.generation if conversation else None,
        environment_id=instance.environment_id,
        request_key=request.request_key,
        fingerprint=fingerprint,
        status="queued",
        encrypted_input=encrypted.ciphertext,
        encrypted_prepared=cipher.encrypt_payload(prepared.model_dump(mode="json")).ciphertext,
        key_id=encrypted.key_id,
    )
    if conversation:
        conversation.next_sequence += 1
    session.add(row)
    session.flush()
    append_event(session, run_id=row.id, kind="queued", payload={}, cipher=cipher)
    return row


def claim_run(
    session: Session,
    *,
    run_id: UUID,
    driver: str,
    cipher: SecretCipher,
    preserve_attempt: bool = False,
) -> tuple[ExecutionContext, PreparedAgent, object, dict[str, object] | None, bool | None]:
    row = session.get(ExecutionRun, run_id)
    if row is None or row.instance_id is None:
        raise ConflictError("The run is not bound to a current Agent instance")
    actor = current_user(session, row.user_id)
    get_instance(session, actor=actor, instance_id=row.instance_id, lock=True)
    conversation = (
        get_session(session, actor=actor, session_id=row.session_id, lock=True)
        if row.session_id
        else None
    )
    row = get_run(session, actor=actor, run_id=run_id, lock=True)
    if row.status != "queued" or row.driver != driver:
        raise ConflictError("The run is not queued for this driver")
    if conversation is not None:
        if conversation.generation != row.generation or conversation.status != "active":
            raise ConflictError("The private history generation changed")
        if session.scalar(
            select(
                exists().where(
                    ExecutionRun.session_id == row.session_id,
                    ExecutionRun.sequence < row.sequence,
                    ExecutionRun.status.in_(ACTIVE),
                )
            )
        ):
            raise AdmissionPending("An earlier conversation run has not finished")
    if row.environment_id is not None:
        environment = get_environment(
            session, actor=actor, environment_id=row.environment_id, lock=True
        )
        if environment.status in {"pending", "busy"} or environment.active_run_id not in {
            None,
            row.id,
        }:
            raise AdmissionPending("The persistent sandbox is occupied")
        if environment.status != "ready" or environment.active_operation_id is not None:
            raise ConflictError("The persistent sandbox is occupied or unavailable")
        environment.active_run_id = row.id
    attempt_id = row.attempt_id if preserve_attempt and row.encrypted_state else uuid4().hex
    assert attempt_id is not None
    reservation = quotas.reserve_capacity(
        session,
        user_id=row.user_id,
        kind="concurrency",
        amount=1,
        idempotency_scope="execution-attempt",
        idempotency_key=f"{row.id}:{attempt_id}:{row.approval_id}",
    )
    row.capacity_id, row.attempt_id, row.status = reservation.id, attempt_id, "running"
    prepared = PreparedAgent.model_validate(cipher.decrypt_payload(row.encrypted_prepared or ""))
    request = RunCreate.model_validate(cipher.decrypt_payload(row.encrypted_input))
    state = cipher.decrypt_payload(row.encrypted_state) if row.encrypted_state else None
    append_event(session, run_id=row.id, kind="running", payload={}, cipher=cipher)
    return (
        ExecutionContext(
            row.id,
            row.user_id,
            prepared.instance_id,
            attempt_id,
            Principal(user_id=actor.user_id, roles=actor.platform_roles),
            prepared.source,
        ),
        prepared,
        request.input,
        state,
        row.approval_decision,
    )


def release_run(session: Session, row: ExecutionRun, *, release_environment: bool) -> None:
    if row.capacity_id is not None:
        quotas.release_capacity(session, user_id=row.user_id, reservation_id=row.capacity_id)
        row.capacity_id = None
    if release_environment and row.environment_id is not None:
        environment = session.get(SandboxEnvironment, row.environment_id, with_for_update=True)
        if environment is not None and environment.active_run_id == row.id:
            environment.active_run_id = None


def finish_run(
    session: Session,
    *,
    context: ExecutionContext,
    status: str,
    cipher: SecretCipher,
    output: object = None,
    native_state: dict[str, object] | None = None,
    pending: list[dict[str, object]] | None = None,
    error_code: str | None = None,
) -> ExecutionRun:
    row = session.get(ExecutionRun, context.run_id, with_for_update=True, populate_existing=True)
    if row is None or row.attempt_id != context.attempt_id:
        raise ConflictError("The execution attempt no longer owns this run")
    if row.status not in {"queued", "running", "interrupted", "cancelling"}:
        return row
    uncertain = session.scalar(
        select(
            exists().where(
                ExecutionOperation.run_id == row.id,
                ExecutionOperation.status.in_(("started", "uncertain")),
            )
        )
    )
    row.status = "needs_attention" if uncertain else status
    row.error_code = "external_effect_unknown" if uncertain else error_code
    if row.status == "interrupted":
        if native_state is None or not pending:
            raise InvalidArgumentError("An interruption requires native state and pending calls")
        row.encrypted_state = cipher.encrypt_payload(native_state).ciphertext
        row.approval_id, row.approval_decision = uuid4(), None
        row.encrypted_result = cipher.encrypt_payload({"pending": pending}).ciphertext
    elif row.status == "completed":
        row.encrypted_result = cipher.encrypt_payload({"output": output}).ciphertext
        row.encrypted_state = None
        row.approval_id = None
    release_run(
        session, row, release_environment=row.status not in {"interrupted", "needs_attention"}
    )
    append_event(
        session,
        run_id=row.id,
        kind=row.status,
        payload={"error_code": row.error_code},
        cipher=cipher,
    )
    return row


def cancel_run(
    session: Session, *, actor: CurrentUser, run_id: UUID, cipher: SecretCipher
) -> ExecutionRun:
    row = get_run(session, actor=actor, run_id=run_id, lock=True)
    return request_cancellation(session, row=row, cipher=cipher)


def request_cancellation(
    session: Session, *, row: ExecutionRun, cipher: SecretCipher
) -> ExecutionRun:
    """Stop an already authorized run; trusted callers must verify ownership and lock it.

    Cleanup remains possible after its user loses execution authority. This function
    grants no authority to execute effects, resume a run or delete its resources.
    """
    if row.status in {"queued", "interrupted"}:
        row.status = "cancelled"
        release_run(session, row, release_environment=True)
    elif row.status == "running":
        row.status = "cancelling"
    append_event(session, run_id=row.id, kind=row.status, payload={}, cipher=cipher)
    return row
