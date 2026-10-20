"""Delegated audit reads retain both invocation and public API authorization."""

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from opsmesh.bootstrap.extensions import ExtensionPublicAPI
from opsmesh.bootstrap.routers import ConfigurationAuditScopeAdmission
from opsmesh.extensions.delegation import DelegatedCall, authenticated_call
from opsmesh.shared.errors import ForbiddenError


@pytest.mark.parametrize("target", ["own", "other_actor", "configuration", "missing"])
def test_user_scoped_audit_read_only_admits_own_unscoped_record(target):
    principal_id = uuid4()
    event = SimpleNamespace(
        actor_id=str(uuid4() if target == "other_actor" else principal_id),
        configuration_id=uuid4() if target == "configuration" else None,
    )
    session = Mock()
    session.get.return_value = None if target == "missing" else event

    def validate():
        ExtensionPublicAPI.validate_scope(
            session,
            "platform.audit.read",
            {"event_id": str(uuid4())},
            {},
            {},
            {},
            principal_id=principal_id,
        )

    if target == "own":
        validate()
    else:
        with pytest.raises(ForbiddenError):
            validate()


def test_user_scoped_invocation_cannot_list_unscoped_audit():
    with pytest.raises(ForbiddenError):
        ExtensionPublicAPI.validate_scope(
            Mock(), "platform.audit.list", {}, {}, {}, {}, principal_id=uuid4()
        )


@pytest.mark.parametrize(
    "target", ["own", "actor", "instance", "configuration", "principal", "missing"]
)
def test_delegated_audit_admission_does_not_require_or_grant_admin(monkeypatch, target):
    principal_id, instance_id, event_id, invocation_id = (uuid4() for _ in range(4))
    invocation = SimpleNamespace(
        principal_id=uuid4() if target == "principal" else principal_id,
        instance_id=instance_id,
        snapshot={},
    )
    event = SimpleNamespace(
        actor_id=str(uuid4() if target == "actor" else principal_id),
        instance_id=uuid4() if target == "instance" else instance_id,
        configuration_id=uuid4() if target == "configuration" else None,
    )
    monkeypatch.setattr("opsmesh.bootstrap.routers.load_invocation", lambda *_: invocation)
    session = Mock()
    session.get.return_value = None if target == "missing" else event
    with authenticated_call(
        DelegatedCall(invocation_id, "audit.read", "GET", f"/custom/platform/audit/{event_id}")
    ):
        if target == "own":
            ConfigurationAuditScopeAdmission().admit(
                session, user_id=principal_id, configuration_id=None
            )
        else:
            with pytest.raises(ForbiddenError):
                ConfigurationAuditScopeAdmission().admit(
                    session, user_id=principal_id, configuration_id=None
                )


def test_unscoped_audit_without_authenticated_delegation_is_rejected():
    with pytest.raises(ForbiddenError):
        ConfigurationAuditScopeAdmission().admit(Mock(), user_id=uuid4(), configuration_id=None)
