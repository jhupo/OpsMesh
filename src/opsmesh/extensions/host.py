"""Authenticated, bounded host RPC with per-occurrence PostgreSQL receipts."""

import asyncio
import logging
from typing import Any
from uuid import UUID

from opsmesh_sdk.host.events import EventDraft
from opsmesh_sdk.host.observability import ObservabilityRecord
from opsmesh_sdk.manifest import PackageManifest
from opsmesh_sdk.transport.contracts import TransportRequest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.extensions import state
from opsmesh.extensions.access import ScopeAuthorization, authorize_invocation
from opsmesh.extensions.contracts import HostOperations
from opsmesh.extensions.events import publish_event
from opsmesh.extensions.invocation import load_invocation
from opsmesh.extensions.models import HostCallReceipt, Invocation
from opsmesh.extensions.schemas import StateBatch, StateMutation
from opsmesh.identity.auth.contracts import UserDirectory
from opsmesh.shared.errors import ConflictError, DomainError, InvalidArgumentError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.security.redaction import redact_sensitive_payload, redaction_rules_for
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.telemetry.metrics import increment_counter, set_gauge
from opsmesh.telemetry.tracing import start_span

logger = logging.getLogger(__name__)


def host_permission(request: TransportRequest) -> str | None:
    path = request.path
    if path.startswith("/host/state"):
        return "host.state.read" if request.method == "GET" else "host.state.write"
    if path == "/host/events/publish":
        return "host.events.publish"
    if path == "/host/observability/records":
        return "host.observability.report"
    return None


class InvocationHost:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        users: UserDirectory,
        scopes: ScopeAuthorization,
        cipher: SecretCipher,
        operations: HostOperations,
    ) -> None:
        self.sessions, self.users, self.scopes = sessions, users, scopes
        self.cipher, self.operations = cipher, operations

    async def exchange(self, invocation_id: UUID, message: dict[str, Any]) -> dict[str, Any]:
        sequence = message.get("id")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or not 1 <= sequence <= 1024:
            raise InvalidArgumentError("Invalid host RPC sequence")
        try:
            request = TransportRequest.model_validate(message.get("request"))
            if request.stream or request.content is not None or request.headers:
                raise InvalidArgumentError("The host accepts bounded JSON requests without headers")
            if len(canonical_json(request.model_dump(mode="json")).encode()) > 1048576:
                raise InvalidArgumentError("The host request exceeds its byte bound")
            result = await self.request(invocation_id, f"rpc:{sequence}", request)
            return {"id": sequence, **result}
        except DomainError as error:
            return {
                "id": sequence,
                "status": error.status_code,
                "body": {"code": error.code, "message": error.message},
            }
        except (ValueError, ValidationError):
            return {
                "id": sequence,
                "status": 400,
                "body": {"code": "invalid_argument", "message": "Invalid host request"},
            }

    async def request(
        self,
        invocation_id: UUID,
        operation_id: str,
        request: TransportRequest,
    ) -> dict[str, Any]:
        permission = host_permission(request)
        fingerprint = payload_digest(request.model_dump(mode="json"))
        with self.sessions() as session, session.begin():
            invocation = load_invocation(session, invocation_id, lock=True)
            user, instance, installation = authorize_invocation(
                session,
                invocation,
                users=self.users,
                scopes=self.scopes,
                permission=permission,
            )
            receipt = session.scalar(
                select(HostCallReceipt).where(
                    HostCallReceipt.invocation_id == invocation_id,
                    HostCallReceipt.operation_id == operation_id,
                )
            )
            if receipt is not None:
                if receipt.fingerprint != fingerprint:
                    raise ConflictError("The RPC occurrence was reused with different content")
                if receipt.status != "completed":
                    raise ConflictError("The host effect needs reconciliation")
                return self.cipher.decrypt_payload(receipt.result_ciphertext or "")
            budget = invocation.snapshot.get("hook_budget")
            if isinstance(budget, dict) and request.path.startswith("/host/models/"):
                requests = [request.model_dump(mode="json")]
                for previous in session.scalars(
                    select(HostCallReceipt).where(HostCallReceipt.invocation_id == invocation.id)
                ):
                    requests.append(self.cipher.decrypt_payload(previous.request_ciphertext))
                upper = 0
                for item in requests:
                    if item.get("path") not in {"/host/models/complete", "/host/models/embed"}:
                        continue
                    body = item.get("body")
                    if not isinstance(body, dict):
                        raise InvalidArgumentError("A model request requires its typed body")
                    prompt = body.get("prompt", {})
                    upper += len(canonical_json(body).encode())
                    if item["path"] == "/host/models/complete":
                        if not isinstance(prompt, dict):
                            raise InvalidArgumentError("Invalid model prompt")
                        upper += int(prompt.get("max_output_tokens") or 4096)
                if upper > int(budget.get("model_tokens", 0)):
                    raise ConflictError("The frozen hook model token budget is exhausted")
            receipt = HostCallReceipt(
                invocation_id=invocation_id,
                operation_id=operation_id,
                fingerprint=fingerprint,
                status="started",
                request_ciphertext=self.cipher.encrypt_payload(
                    request.model_dump(mode="json")
                ).ciphertext,
            )
            session.add(receipt)
            session.flush()
            receipt_id = receipt.id
            if permission is not None:
                if (
                    request.path == "/host/state/batch"
                    and isinstance(request.body, dict)
                    and request.body.get("event_drafts")
                ):
                    authorize_invocation(
                        session,
                        invocation,
                        users=self.users,
                        scopes=self.scopes,
                        permission="host.events.publish",
                    )
                body = self.local_request(
                    session,
                    invocation,
                    request,
                    PackageManifest.model_validate(installation.manifest),
                    limit_bytes=instance.state_limit_bytes,
                )
                result = {"status": 200, "body": body}
                receipt.status = "completed"
                receipt.result_ciphertext = self.cipher.encrypt_payload(result).ciphertext
                return result
        # Remote/other-owner calls run after the receipt transaction commits.
        # Retries cannot repeat an external effect whose answer was lost.
        try:
            result = await self.operations.request(
                invocation_id=invocation_id,
                operation_id=operation_id,
                request=request.model_dump(mode="json"),
            )
        except BaseException:
            await asyncio.shield(asyncio.to_thread(self.finish, receipt_id, None))
            raise
        await asyncio.to_thread(self.finish, receipt_id, result)
        return result

    def finish(self, receipt_id: UUID, result: dict[str, Any] | None) -> None:
        with self.sessions() as session, session.begin():
            receipt = session.get(HostCallReceipt, receipt_id, with_for_update=True)
            assert receipt is not None
            receipt.status = "completed" if result is not None else "needs_attention"
            receipt.revision += 1
            if result is not None:
                receipt.result_ciphertext = self.cipher.encrypt_payload(result).ciphertext

    def local_request(
        self,
        session: Session,
        invocation: Invocation,
        request: TransportRequest,
        manifest: PackageManifest,
        *,
        limit_bytes: int,
    ) -> Any:
        path, method = request.path, request.method
        body = request.body if isinstance(request.body, dict) else {}
        namespace_id = invocation.namespace_id
        if path == "/host/state/capacity" and method == "GET":
            namespace = state.lock_namespace(session, namespace_id)
            return {"used_bytes": namespace.used_bytes, "limit_bytes": limit_bytes}
        if path == "/host/state" and method == "GET":
            return state.list_values(
                session,
                namespace_id,
                prefix=request.query.get("prefix", ""),
                cursor=request.query.get("cursor", ""),
            )
        if path == "/host/state/batch" and method == "POST":
            batch = StateBatch.model_validate(body)
            state.apply_mutations(
                session, namespace_id=namespace_id, entries=batch.entries, limit_bytes=limit_bytes
            )
            for index, draft in enumerate(batch.event_drafts):
                self.publish(
                    session,
                    invocation,
                    manifest,
                    draft,
                    f"batch:{invocation.id}:{payload_digest(body)}:{index}",
                )
            return {"committed": True}
        if path == "/host/state/entries":
            key = request.query.get("key", "")
            if method == "GET":
                return state.read_value(session, namespace_id, key)
            if method == "POST":
                entry = StateMutation(
                    key=key, value=body.get("value"), expected_version=body.get("expected_version")
                )
                current = state.read_value(session, namespace_id, key)
                if entry.expected_version != current["version"]:
                    return {"swapped": False}
            elif method == "PUT":
                entry = StateMutation(key=key, value=body.get("value"))
            elif method == "DELETE":
                entry = StateMutation(key=key, delete=True)
            else:
                raise InvalidArgumentError("Unknown state operation")
            state.apply_mutations(
                session, namespace_id=namespace_id, entries=[entry], limit_bytes=limit_bytes
            )
            return {"swapped": True, **state.read_value(session, namespace_id, key)}
        if path == "/host/events/publish" and method == "POST":
            if not request.idempotency_key:
                raise InvalidArgumentError("Event publishing requires an idempotency key")
            return self.publish(session, invocation, manifest, body, request.idempotency_key)
        if path == "/host/observability/records" and method == "POST":
            record = ObservabilityRecord.model_validate(body)
            if len(canonical_json(body).encode()) > 16384:
                raise InvalidArgumentError("Observability record exceeds its byte bound")
            # Free text can contain model/user content. Only bounded structured,
            # redacted metadata is accepted; identity comes from this invocation.
            redacted = redact_sensitive_payload(
                record.model_dump(mode="json"), rules=redaction_rules_for(session)
            )
            if record.kind == "metric":
                if record.value is None or (record.instrument == "counter" and record.value < 0):
                    raise InvalidArgumentError("Metrics require a valid instrument value")
                labels = {"namespace": manifest.package_name, "name": record.name}
                if record.instrument == "counter":
                    increment_counter(
                        "opsmesh_extension_operations", labels=labels, amount=record.value
                    )
                else:
                    set_gauge("opsmesh_extension_value", record.value, labels=labels)
            elif record.kind == "trace":
                with start_span(
                    "extension.record",
                    scope="opsmesh.extensions",
                    attributes={
                        "extension.namespace": manifest.package_name,
                        "extension.invocation_id": str(invocation.id),
                        "extension.record": canonical_json(redacted),
                    },
                ):
                    pass
            else:
                logger.info(
                    "extension_record",
                    extra={"invocation_id": str(invocation.id), "record": redacted},
                )
            return {"accepted": True}
        raise InvalidArgumentError("Unknown host operation")

    @staticmethod
    def publish(
        session: Session,
        invocation: Invocation,
        manifest: PackageManifest,
        body: dict[str, Any],
        operation_id: str,
    ) -> dict[str, Any]:
        draft = EventDraft.model_validate(body)
        event = publish_event(
            session,
            invocation=invocation,
            manifest=manifest,
            operation_id=operation_id,
            event_type=draft.event_type,
            payload=draft.model_dump(mode="json"),
        )
        return {"event_id": str(event.id)}
