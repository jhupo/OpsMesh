"""Current platform surface, frozen initial DDL and audit access checks."""

import re
from dataclasses import replace
from io import StringIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from opsmesh.bootstrap.api import create_app
from opsmesh.bootstrap.models import register_models
from opsmesh.bootstrap.schema import RETIRED_TABLES
from opsmesh.config import Settings
from opsmesh.platform.audit.models import AuditEvent
from opsmesh.platform.audit.service import get_event
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.security.redaction import require_redaction_rules
from opsmesh.users.errors import UserDisabledError


def test_removed_governance_and_admission_are_absent_from_the_public_contract():
    document = create_app(Settings()).openapi()
    paths = document["paths"]
    assert not any("/platform/approvals" in path or "/platform/policies" in path for path in paths)
    assert "/api/v1/platform/operations/admission" not in paths
    assert set(paths["/api/v1/platform/audit"]) == {"get"}
    audit = document["components"]["schemas"]["AuditEventView"]["properties"]
    assert "configuration_id" not in audit
    assert "configuration_revision" not in audit
    assert "/api/v1/health" in paths
    assert not any("/platform/operations" in path for path in paths)


def test_initial_migration_ddl_matches_the_registered_table_set():
    output = StringIO()
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"), output_buffer=output)
    config.set_main_option("sqlalchemy.url", "postgresql+psycopg://unused/unused")
    command.upgrade(config, "head", sql=True)
    ddl = output.getvalue()
    created = set(re.findall(r"CREATE TABLE (\w+)\s*\(", ddl)) - {"alembic_version"}
    assert created == set(register_models().tables) | RETIRED_TABLES
    assert not created.intersection(
        {
            "approval_requests",
            "platform_policies",
            "platform_policy_revisions",
            "audit_append_receipts",
            "platform_admission",
            "platform_operations",
        }
    )
    assert "configuration_id" not in ddl


def test_audit_read_refuses_other_users_but_allows_owner_and_admin(audit_readers):
    admin, alice, bob = audit_readers
    event = AuditEvent(actor_id=str(alice.user_id))
    session = Mock(spec=Session)
    session.get.return_value = event
    from uuid import uuid4

    event_id = uuid4()
    assert get_event(session, event_id, reader=alice) is event
    assert get_event(session, event_id, reader=admin) is event
    with pytest.raises(NotFoundError):
        get_event(session, event_id, reader=bob)
    session.get.return_value = None
    with pytest.raises(NotFoundError):
        get_event(session, event_id, reader=alice)


def test_disabled_administrator_cannot_read_audit(audit_readers):
    admin = replace(audit_readers[0], status="disabled")
    session = Mock(spec=Session)
    from uuid import uuid4

    with pytest.raises(UserDisabledError):
        get_event(session, uuid4(), reader=admin)
    session.get.assert_not_called()


@pytest.mark.parametrize("value", [None, {}, "empty"])
def test_missing_or_invalid_redaction_rules_do_not_silently_disable_redaction(value):
    with pytest.raises(RuntimeError, match="Redaction rules"):
        require_redaction_rules(value)
