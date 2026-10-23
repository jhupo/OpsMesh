"""Current audit API fixtures; PostgreSQL stays real, identity is supplied by the test."""

from uuid import uuid4

import pytest
from fastapi import FastAPI, Header

from opsmesh.platform.audit.contracts import AuditActor, AuditRecord, record_audit
from opsmesh.platform.audit.routes import router
from opsmesh.shared.http.errors import register_error_handlers
from opsmesh.users.access import CurrentUser
from opsmesh.users.auth.dependencies import current_user
from opsmesh.users.errors import AuthenticationError


@pytest.fixture
def audit_readers():
    return tuple(
        CurrentUser(
            user_id=uuid4(),
            email=f"{name}@example.com",
            display_name=name,
            status="active",
            platform_roles=frozenset({"admin" if name == "admin" else "member"}),
        )
        for name in ("admin", "alice", "bob")
    )


@pytest.fixture
def audit_facts(database_sessions, audit_readers):
    facts = []
    with database_sessions() as session, session.begin():
        for user in audit_readers:
            for _ in range(2):
                event = AuditRecord(
                    "operation",
                    "sample.changed",
                    AuditActor.from_user(user),
                    "platform",
                    summary="Changed",
                    payload_json='{"password":"secret-value","outcome":"ok"}',
                )
                record_audit(session, event)
                facts.append(event)
    return facts


@pytest.fixture
def audit_app(database_sessions, audit_readers):
    app = FastAPI()
    app.state.database_sessions = database_sessions
    app.include_router(router, prefix="/api/v1")
    register_error_handlers(app)
    identities = {str(user.user_id): user for user in audit_readers}

    def authenticated_reader(x_session_id: str = Header(alias="X-Session-ID")):
        if x_session_id not in identities:
            raise AuthenticationError("Unknown test session")
        return identities[x_session_id]

    app.dependency_overrides[current_user] = authenticated_reader
    return app
