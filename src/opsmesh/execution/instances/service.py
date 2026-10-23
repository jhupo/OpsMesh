"""Create, inspect and explicitly retire persistent Agent instances."""

from uuid import UUID, uuid4

from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session

from opsmesh.execution.instances.access import current_user, get_instance
from opsmesh.execution.instances.models import AgentInstance
from opsmesh.execution.instances.schemas import InstanceCreate
from opsmesh.execution.preparation import DefinitionLoader, freeze_agent
from opsmesh.execution.runs.models import ACTIVE, ExecutionRun
from opsmesh.execution.runtime.access import get_environment
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.users.access import CurrentUser


def create_instance(
    session: Session,
    *,
    actor: CurrentUser,
    request: InstanceCreate,
    load: DefinitionLoader,
    cipher: SecretCipher,
) -> AgentInstance:
    current_user(session, actor.user_id)
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"instance:{actor.user_id}:{request.request_key}"},
    )
    fingerprint = payload_digest(request.model_dump(mode="json"))
    existing = session.scalar(
        select(AgentInstance).where(
            AgentInstance.owner_user_id == actor.user_id,
            AgentInstance.request_key == request.request_key,
        )
    )
    if existing is not None:
        if existing.fingerprint != fingerprint:
            raise ConflictError("The instance request key was reused with different input")
        return existing
    snapshot = freeze_agent(request.agent_id, load)
    payload = snapshot.model_dump(mode="json")
    if len(canonical_json(payload).encode()) > 1_000_000:
        raise InvalidArgumentError("The Agent configuration snapshot is too large")
    if snapshot.requires_sandbox != (request.environment_id is not None):
        raise InvalidArgumentError("A sandbox configuration requires a persistent environment")
    if request.environment_id is not None:
        environment = get_environment(
            session, actor=actor, environment_id=request.environment_id, lock=True
        )
        if environment.status != "ready":
            raise ConflictError("The sandbox environment is not ready")
    encrypted = cipher.encrypt_payload(payload)
    name = request.name or snapshot.definitions[snapshot.root_id].name
    if len(name) > 200:
        raise InvalidArgumentError("The Agent instance name exceeds its size limit")
    row = AgentInstance(
        id=uuid4(),
        owner_user_id=actor.user_id,
        agent_id=request.agent_id,
        name=name,
        kind="sandbox" if snapshot.requires_sandbox else "agent",
        status="active",
        environment_id=request.environment_id,
        request_key=request.request_key,
        fingerprint=fingerprint,
        encrypted_config=encrypted.ciphertext,
        key_id=encrypted.key_id,
    )
    session.add(row)
    session.flush()
    return row


def delete_instance(session: Session, *, actor: CurrentUser, instance_id: UUID) -> None:
    row = get_instance(session, actor=actor, instance_id=instance_id, lock=True)
    if session.scalar(
        select(exists().where(ExecutionRun.instance_id == row.id, ExecutionRun.status.in_(ACTIVE)))
    ):
        raise ConflictError("The Agent instance has unfinished runs")
    # The environment and private conversations have separate explicit lifecycles.
    row.status = "deleted"
