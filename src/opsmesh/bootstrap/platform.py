"""Register concrete maintenance owners and admission controls explicitly."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.channels.service import unsettled_delivery_count
from opsmesh.extensions.maintenance import stop_deployments, unsettled_extension_count
from opsmesh.identity.auth.maintenance import cleanup_expired_sessions
from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.runs.maintenance import unsettled_execution_count, unsettled_flow_count
from opsmesh.orchestration.triggers.service import reencrypt_webhook_secrets
from opsmesh.platform.model_connections.service import reencrypt_model_keys
from opsmesh.platform.operations.admission import set_drained
from opsmesh.platform.operations.handlers import OperationHandler, OperationHandlers, OperationPage
from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.shared.security.encryption import SecretCipher


class CredentialReencryptor(Protocol):
    def __call__(
        self, session: Session, *, cipher: SecretCipher, cursor: str | None, limit: int
    ) -> tuple[int, str | None]: ...


def active_work(session: Session) -> dict[str, int]:
    return {
        "execution": unsettled_execution_count(session),
        "extensions": unsettled_extension_count(session),
        "orchestration": unsettled_flow_count(session),
        "channels": unsettled_delivery_count(session),
    }


def operation_handlers(cipher: SecretCipher) -> OperationHandlers:
    def drain(
        session: Session,
        target: dict[str, object],
        cursor: str | None,
        limit: int,
        actor: CurrentUser,
        operation_id: UUID,
    ) -> OperationPage:
        if target:
            raise InvalidArgumentError("Admission commands take no target arguments")
        set_drained(session, drained=True)
        stop_deployments(session, actor=actor)
        return OperationPage(details={"drained": True})

    def resume(
        session: Session,
        target: dict[str, object],
        cursor: str | None,
        limit: int,
        actor: CurrentUser,
        operation_id: UUID,
    ) -> OperationPage:
        if target:
            raise InvalidArgumentError("Admission commands take no target arguments")
        set_drained(session, drained=False)
        return OperationPage(details={"drained": False})

    def cleanup(
        session: Session,
        target: dict[str, object],
        cursor: str | None,
        limit: int,
        actor: CurrentUser,
        operation_id: UUID,
    ) -> OperationPage:
        if set(target) != {"before"} or not isinstance(target["before"], str):
            raise InvalidArgumentError("Session cleanup requires an explicit before timestamp")
        cutoff = datetime.fromisoformat(target["before"])
        if cutoff.tzinfo is None:
            raise InvalidArgumentError("Session cleanup timestamp requires a timezone")
        count, next_cursor = cleanup_expired_sessions(
            session, cutoff=cutoff, cursor=cursor, limit=limit
        )
        return OperationPage(processed=count, next_cursor=next_cursor)

    def rotation_handler(owner: CredentialReencryptor) -> OperationHandler:
        def rotate(
            session: Session,
            target: dict[str, object],
            cursor: str | None,
            limit: int,
            actor: CurrentUser,
            operation_id: UUID,
        ) -> OperationPage:
            if target != {"key_id": cipher.current_key_id}:
                raise InvalidArgumentError(
                    "Rotation must name the configured current encryption key"
                )
            count, next_cursor = owner(session, cipher=cipher, cursor=cursor, limit=limit)
            return OperationPage(
                processed=count, next_cursor=next_cursor, details={"key_id": cipher.current_key_id}
            )

        return rotate

    return {
        ("platform", "admission.drain"): drain,
        ("platform", "admission.resume"): resume,
        ("identity.sessions", "cleanup"): cleanup,
        ("orchestration.webhooks", "rotate"): rotation_handler(reencrypt_webhook_secrets),
        ("platform.model_connections", "rotate"): rotation_handler(reencrypt_model_keys),
    }


def transaction_dependencies() -> dict[str, object]:
    """Named, mandatory transaction dependencies; no registration or result aggregation."""
    from functools import partial

    from opsmesh.bootstrap.orchestration import ConfigurationOperations
    from opsmesh.platform.audit.service import append_audit_record
    from opsmesh.platform.policies.redaction import PLATFORM_REDACTION
    from opsmesh.platform.policies.service import current_decision

    return {
        "configuration_access": ConfigurationOperations(),
        "audit_writer": partial(append_audit_record, rules=PLATFORM_REDACTION),
        "policy_checker": current_decision,
        "redaction_rules": PLATFORM_REDACTION,
    }
