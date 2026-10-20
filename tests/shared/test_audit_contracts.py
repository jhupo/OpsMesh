"""Audit inputs and mandatory direct policy dependencies."""

from dataclasses import replace

import pytest
from sqlalchemy.orm import Session

from opsmesh.shared.audit import AuditActor, AuditRecord, record_audit
from opsmesh.shared.errors import ForbiddenError
from opsmesh.shared.policies import enforce_operation_policy


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


def test_missing_mandatory_dependencies_fail_closed():
    with Session() as session:
        with pytest.raises(RuntimeError, match="audit writer"):
            record_audit(
                session, AuditRecord("operation", "created", AuditActor("platform"), "platform")
            )
        with pytest.raises(RuntimeError, match="policy checker"):
            enforce_operation_policy(session, action="tool.invoke", name="write")


@pytest.mark.parametrize("decision", ["deny", "require_approval", "unknown"])
def test_policy_call_cannot_silently_allow_invalid_or_unapproved_operations(decision):
    with (
        Session(info={"policy_checker": lambda *args, **kwargs: decision}) as session,
        pytest.raises((ForbiddenError, RuntimeError)),
    ):
        enforce_operation_policy(session, action="tool.invoke", name="write")
