"""Application audit writers and shared sensitive-data protection policies."""

import re
from functools import partial
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.platform.audit.contracts import AuditActor, AuditRecord, record_audit
from opsmesh.platform.audit.service import append_audit_record
from opsmesh.shared.security.redaction import RedactionRules

_EXACT_KEYS = {
    "base_url",
    "container_id",
    "ciphertext",
    "credential",
    "credentials",
    "docker_container_id",
    "endpoint_url",
    "external_ref",
    "remote_url",
    "storage_key",
    "session_id",
    "invitation_code",
}
_KEY_PARTS = {
    "api_key",
    "authorization",
    "cookie",
    "headers",
    "password",
    "secret",
    "token",
}
_TEXT_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{2,}\b", re.IGNORECASE),
    re.compile(r"\b(?:ccut|gh[opsu]|github_pat)_[A-Za-z0-9_=-]{8,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{12,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{6,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_ -]?key|authorization|password|(?:client[_ -]?)?secret|"
        r"(?:(?:access|refresh)[_ -]?)?token|base[_ -]?url|"
        r"(?:x[_ -]?)?session[_ -]?id|invitation[_ -]?code)\s*[:=]\s*"
        r"['\"]?[^\s,'\";}]+['\"]?",
        re.IGNORECASE,
    ),
)

PLATFORM_REDACTION = RedactionRules(
    exact_keys=frozenset(_EXACT_KEYS),
    key_parts=frozenset(_KEY_PARTS),
    text_patterns=_TEXT_PATTERNS,
)


def gateway_audit(session: Session, action: str, actor_id: UUID, target_id: UUID) -> None:
    record_audit(
        session,
        AuditRecord(
            category="gateway",
            action=action,
            actor=AuditActor(actor_type="user", actor_id=str(actor_id)),
            source="gateway",
            target_type=action.split(".", 1)[0],
            target_id=str(target_id),
        ),
    )


def transaction_dependencies() -> dict[str, object]:
    return {
        "audit_writer": partial(append_audit_record, rules=PLATFORM_REDACTION),
        "redaction_rules": PLATFORM_REDACTION,
        "gateway_audit": gateway_audit,
    }
