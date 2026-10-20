"""Execution-owned one-shot model operations, using the native model SDK and ledgers."""

import asyncio
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import httpx2
from agents import ModelSettings, OpenAIProvider
from agents.models.interface import ModelTracing
from openai import AsyncOpenAI
from openai.types.shared import Reasoning
from opsmesh_sdk.values import ReasoningEffort
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column
from temporalio import activity

from opsmesh.identity.access import require_platform_admin
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.quotas.schemas import NormalizedUsage
from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject
from opsmesh.orchestration.runs.model_activities import ExecutionModelProvider
from opsmesh.orchestration.runs.usage import normalize_usage
from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError, DomainError, ForbiddenError, InvalidArgumentError
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.temporal.dispatch.outbox import stage_execution


class ModelOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    configuration_id: UUID | None = None
    connection_id: UUID
    model: str = Field(min_length=1, max_length=200)
    kind: Literal["complete", "embed"]
    inputs: list[str] = Field(min_length=1, max_length=512)
    max_output_tokens: int = Field(default=4096, ge=1, le=131072)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: ReasoningEffort | None = None
    source_ref: UUID


class ModelOperation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "execution_model_operations"
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    configuration_id: Mapped[UUID | None]
    source_ref: Mapped[UUID]
    status: Mapped[str] = mapped_column(String(20), default="queued")
    request_ciphertext: Mapped[str] = mapped_column(Text)
    result_ciphertext: Mapped[str | None] = mapped_column(Text)
    reconciliation_ciphertext: Mapped[str | None] = mapped_column(Text)
    token_reservation_id: Mapped[UUID | None]
    capacity_reservation_id: Mapped[UUID | None]
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    revision: Mapped[int] = mapped_column(default=0)


class ModelOperationWork(BaseModel):
    execution_id: UUID
    workflow_id: str
    workflow_name: str = "ExecutionModelOperationWorkflow"


class ModelOperationReconciliation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_revision: int = Field(ge=0)
    outcome: Literal["succeeded", "not_executed"]
    actual_usage: NormalizedUsage | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(min_length=1)


class ModelOperationAuthorization(Protocol):
    def authorize_submission(
        self, session: Session, *, principal_id: UUID, request: ModelOperationRequest
    ) -> None: ...
    def admit(
        self, session: Session, *, principal_id: UUID, request: ModelOperationRequest
    ) -> None: ...


class ModelOperations:
    def __init__(
        self, provider: ExecutionModelProvider, authorization: ModelOperationAuthorization
    ) -> None:
        self.provider, self.authorization = provider, authorization

    def submit(
        self,
        session: Session,
        *,
        principal_id: UUID,
        request: ModelOperationRequest,
        operation_id: str,
    ) -> ModelOperation:
        self.authorization.authorize_submission(session, principal_id=principal_id, request=request)
        self.authorization.admit(session, principal_id=principal_id, request=request)
        if sum(len(value.encode()) for value in request.inputs) > 262144:
            raise ConflictError("The model input exceeds its byte bound")

        def create() -> ModelOperation:
            identifier = uuid4()
            record = ModelOperation(
                id=identifier,
                principal_id=principal_id,
                configuration_id=request.configuration_id,
                source_ref=request.source_ref,
                request_ciphertext=self.provider.cipher.encrypt_payload(
                    request.model_dump(mode="json")
                ).ciphertext,
            )
            session.add(record)
            session.flush()
            stage_execution(
                session,
                ModelOperationWork(
                    execution_id=identifier, workflow_id=f"model-operation:{identifier}"
                ),
            )
            return record

        def existing(identifier: UUID) -> ModelOperation:
            record = session.get(ModelOperation, identifier)
            if record is None or record.principal_id != principal_id:
                raise ForbiddenError("The model operation is unavailable")
            return record

        return run_idempotent_create(
            session=session,
            scope_id=request.configuration_id or principal_id,
            actor_id=principal_id,
            operation="orchestration.model_operation",
            idempotency_key=operation_id,
            request_payload=request.model_dump(mode="json"),
            create=create,
            get_existing=existing,
            resource_id=lambda record: record.id,
        )

    def claim(self, identifier: UUID) -> tuple[ModelOperationRequest, Any] | None:
        p = self.provider
        with p.sessions() as session, session.begin():
            record = session.get(ModelOperation, identifier)
            if record is None:
                raise ConflictError("The model operation is unavailable")
            request = ModelOperationRequest.model_validate(
                p.cipher.decrypt_payload(record.request_ciphertext)
            )
            if record.status == "queued":
                # The source owner locks its parent operation before the child
                # and before any quantity ledger locks.
                self.authorization.admit(session, principal_id=record.principal_id, request=request)
            session.refresh(record, with_for_update=True)
            if record.status == "started":
                self.release_capacity(session, record)
                record.status = "needs_attention"
                record.revision += 1
                return None
            if record.status != "queued":
                return None
            scope = ExecutionScope(
                configuration_id=request.configuration_id,
            )
            p.admission.admit(session, subject=ExecutionSubject(record.principal_id), scope=scope)
            connection = p.connections.resolve(
                session,
                subject=ExecutionSubject(record.principal_id),
                scope=scope,
                connection_id=request.connection_id,
                model=request.model,
            )
            if request.kind == "complete" and request.max_output_tokens > connection.context_tokens:
                raise ConflictError("The model output bound exceeds its verified context")
            input_bound = connection.context_tokens
            if request.kind == "embed":
                input_bounds = [len(value.encode()) + 32 for value in request.inputs]
                if any(bound > connection.context_tokens for bound in input_bounds):
                    raise ConflictError("An embedding input exceeds its verified context bound")
                # Embedding usage sums every input in the batch, rather than
                # sharing one completion context window across the request.
                input_bound = sum(input_bounds)
            upper = NormalizedUsage(
                input_tokens=input_bound,
                cached_input_tokens=connection.context_tokens if request.kind == "complete" else 0,
                output_tokens=request.max_output_tokens if request.kind == "complete" else 0,
            )
            if p.bindings.limit_operation is None:
                raise ConflictError("Source Token admission is not assembled")
            p.bindings.limit_operation(
                session,
                run_ref=str(identifier),
                operation_id=f"model-operation:{identifier}",
                tokens=input_bound + upper.output_tokens,
            )
            token = p.bindings.tokens.reserve_tokens(
                session,
                user_id=record.principal_id,
                amount=input_bound + upper.output_tokens,
                idempotency_scope="orchestration.model_operation",
                idempotency_key=str(identifier),
            )
            capacity = p.bindings.capacity.reserve_capacity(
                session,
                user_id=record.principal_id,
                kind="concurrency",
                amount=1,
                idempotency_scope="orchestration.model_operation",
                idempotency_key=str(identifier),
            )
            record.token_reservation_id = token.id
            record.capacity_reservation_id = capacity.id
            record.status = "started"
            record.revision += 1
            return request, connection

    @staticmethod
    def actor(record: ModelOperation) -> AuditActor:
        return AuditActor(
            actor_type="user",
            actor_id=str(record.principal_id),
            configuration_id=record.configuration_id,
        )

    def complete(
        self, identifier: UUID, result: dict[str, Any] | None, usage: NormalizedUsage | None
    ) -> None:
        p = self.provider
        with p.sessions() as session, session.begin():
            record = session.get(ModelOperation, identifier, with_for_update=True)
            assert record is not None
            if record.status == "completed":
                return
            if usage is None:
                self.release_capacity(session, record)
                record.status = "needs_attention"
            else:
                assert record.token_reservation_id is not None
                p.bindings.tokens.settle_tokens(
                    session,
                    reservation_id=record.token_reservation_id,
                    actual_amount=usage.input_tokens
                    + usage.cached_input_tokens
                    + usage.output_tokens,
                )
                self.release_capacity(session, record)
                record.usage = usage.model_dump()
                record.result_ciphertext = p.cipher.encrypt_payload(result or {}).ciphertext
                record.status = "completed"
            record.revision += 1

    def release_capacity(self, session: Session, record: ModelOperation) -> None:
        if record.capacity_reservation_id:
            self.provider.bindings.capacity.release_capacity(
                session,
                user_id=record.principal_id,
                reservation_id=record.capacity_reservation_id,
            )
            record.capacity_reservation_id = None

    def reconcile(
        self,
        session: Session,
        record: ModelOperation,
        request: ModelOperationReconciliation,
        *,
        actor: CurrentUser,
    ) -> None:
        require_platform_admin(actor)
        if record.status != "needs_attention" or record.revision != request.expected_revision:
            raise ConflictError("The model operation is not awaiting this reconciliation")
        if record.token_reservation_id is None:
            raise ConflictError("The model operation has no held token reservation")
        if len(str(request.model_dump()).encode()) > 1048576:
            raise InvalidArgumentError("The reconciliation exceeds its byte bound")
        if request.outcome == "succeeded":
            usage = request.actual_usage
            if usage is None:
                raise InvalidArgumentError("A confirmed model result requires actual usage")
            self.provider.bindings.tokens.settle_tokens(
                session,
                reservation_id=record.token_reservation_id,
                actual_amount=usage.input_tokens + usage.cached_input_tokens + usage.output_tokens,
            )
            record.usage = usage.model_dump()
            record.result_ciphertext = self.provider.cipher.encrypt_payload(
                request.result
            ).ciphertext
        else:
            self.provider.bindings.tokens.release_tokens(
                session, reservation_id=record.token_reservation_id
            )
        self.release_capacity(session, record)
        record.reconciliation_ciphertext = self.provider.cipher.encrypt_payload(
            {
                "request": request.model_dump(mode="json"),
                "confirmed_by": str(actor.user_id),
            }
        ).ciphertext
        record_audit(
            session,
            AuditRecord(
                category="audit",
                action="model_operation.reconciled",
                actor=AuditActor.from_user(actor),
                source="platform",
                target_type="model_operation",
                target_id=str(record.id),
                summary="Model operation outcome confirmed",
                payload_json=canonical_json(
                    {
                        "outcome": request.outcome,
                        "evidence_digest": payload_digest(request.evidence),
                        "revision": record.revision,
                    }
                ),
            ),
        )
        record.status = "completed" if request.outcome == "succeeded" else "failed"
        record.revision += 1

    @activity.defn(name="orchestration.model_operation")
    async def execute(self, work: ModelOperationWork) -> None:
        try:
            claim = await asyncio.to_thread(self.claim, work.execution_id)
        except DomainError:
            with self.provider.sessions() as session, session.begin():
                record = session.get(ModelOperation, work.execution_id, with_for_update=True)
                if record is not None and record.status == "queued":
                    record.status = "failed"
                    record.revision += 1
            return
        if claim is None:
            return
        request, connection = claim
        result: dict[str, Any] | None = None
        usage = None
        try:
            async with AsyncOpenAI(
                api_key=connection.api_key,
                base_url=connection.base_url,
                max_retries=0,
                timeout=120,
                http_client=httpx2.AsyncClient(
                    trust_env=False,
                    follow_redirects=False,
                ),
            ) as client:
                if request.kind == "embed":
                    response = await client.embeddings.create(
                        model=request.model, input=request.inputs
                    )
                    result = {
                        "vectors": [
                            value.embedding
                            for value in sorted(response.data, key=lambda value: value.index)
                        ]
                    }
                    usage = NormalizedUsage(input_tokens=response.usage.prompt_tokens)
                else:
                    native = OpenAIProvider(openai_client=client).get_model(request.model)
                    answer = await native.get_response(
                        system_instructions=None,
                        input=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": "\n".join(request.inputs)}
                                ],
                            }
                        ],
                        model_settings=ModelSettings(
                            max_tokens=request.max_output_tokens,
                            temperature=request.temperature,
                            reasoning=Reasoning(effort=request.reasoning_effort)
                            if request.reasoning_effort is not None
                            else None,
                        ),
                        tools=[],
                        output_schema=None,
                        handoffs=[],
                        tracing=ModelTracing.DISABLED,
                        previous_response_id=None,
                        conversation_id=None,
                        prompt=None,
                    )
                    blocks = [
                        {"block_type": "text", "text": part.text}
                        for item in answer.output
                        if item.type == "message"
                        for part in item.content
                        if part.type == "output_text"
                    ]
                    usage = normalize_usage(answer.usage)
                    result = {
                        "blocks": blocks,
                        "usage": {
                            "input_tokens": usage.input_tokens + usage.cached_input_tokens,
                            "output_tokens": usage.output_tokens,
                            "total_tokens": usage.input_tokens
                            + usage.cached_input_tokens
                            + usage.output_tokens,
                            "requests": 1,
                        },
                        "finish_reason": "completed",
                    }
        finally:
            await asyncio.shield(asyncio.to_thread(self.complete, work.execution_id, result, usage))
