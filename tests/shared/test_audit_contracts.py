"""Immutable audit inputs and mandatory transactional audit recording."""

from dataclasses import replace

import pytest
from sqlalchemy.orm import Session

from opsmesh.platform.audit.contracts import AuditActor, AuditRecord, record_audit


def test_audit_payload_is_detached_and_rejects_non_json_objects():
    record = AuditRecord(
        "security",
        "login.failed",
        AuditActor("platform"),
        "platform",
        payload_json='{"result": "denied"}',
    )
    payload = record.payload
    payload["result"] = "changed"
    assert record.payload == {"result": "denied"}
    for value in ('{"number": NaN}', "[]", '{"text":"' + "x" * 65536 + '"}'):
        with pytest.raises(ValueError):
            replace(record, payload_json=value)


def test_missing_audit_writer_fails_closed():
    with Session() as session, pytest.raises(RuntimeError, match="audit writer"):
        record_audit(
            session, AuditRecord("operation", "created", AuditActor("platform"), "platform")
        )
