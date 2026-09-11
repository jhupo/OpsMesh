from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.agent_runtime.core.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeContext,
    AgentRuntimeUsage,
)
from backend.app.agents.models import AgentProfile
from backend.app.observability.audit_models import AuditEvent
from backend.app.core.config import Settings, get_settings
from backend.app.observability.cost_service import CostAccountingService
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.db.session import get_db_session
from backend.app.identity.models import User
from backend.app.main import create_app
from backend.app.runs.models import AgentRun
from backend.app.workspaces.models import Workspace, WorkspaceMember

TOKEN = "cost-api-token"


def test_cost_api_is_workspace_scoped_audited_and_returns_summary() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, "cost-owner", "cost-api")
    other_owner, other_workspace = _seed_workspace(session, "other-owner", "other-cost")
    now = datetime.now(UTC)

    pricing = client.post(
        f"/api/v1/workspaces/{workspace.id}/costs/pricing-rules",
        headers=_headers(owner.id),
        json={
            "provider": "openai",
            "model": "gpt-cost-api",
            "version": "2026-09",
            "currency": "USD",
            "input_rate_per_million": "2",
            "output_rate_per_million": "10",
            "cached_input_rate_per_million": "1",
            "request_rate": "0.01",
            "effective_from": (now - timedelta(days=1)).isoformat(),
            "source": "contract",
        },
    )
    budget = client.put(
        f"/api/v1/workspaces/{workspace.id}/costs/budget",
        headers=_headers(owner.id),
        json={
            "currency": "USD",
            "monthly_limit": "100",
            "warning_ratio": "0.8",
            "enforcement": "warn",
            "enabled": True,
        },
    )
    forbidden = client.get(
        f"/api/v1/workspaces/{workspace.id}/costs/pricing-rules",
        headers=_headers(other_owner.id),
    )

    assert pricing.status_code == 201
    assert pricing.json()["version"] == "2026-09"
    assert budget.status_code == 200
    assert budget.json()["monthly_limit"] == "100.000000000000"
    assert forbidden.status_code == 403

    profile = AgentProfile(
        workspace_id=workspace.id,
        name="Cost API agent",
        role="operator",
        model="gpt-cost-api",
    )
    session.add(profile)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        agent_profile_id=profile.id,
        model="gpt-cost-api",
        input={},
    )
    session.add(run)
    session.flush()
    CostAccountingService(session).record_usage(
        run=run,
        request=AgentRunRequest(
            agent_profile=profile,
            input_text="test",
            context=AgentRuntimeContext(
                workspace_id=workspace.id,
                task_id=None,
                run_id=run.id,
            ),
            provider="openai",
            model="gpt-cost-api",
        ),
        result=AgentRunResult(
            final_output="done",
            usage=AgentRuntimeUsage(
                request_count=1,
                input_tokens=1_000,
                output_tokens=500,
                total_tokens=1_500,
            ),
        ),
        job_attempt=0,
        occurred_at=now,
    )
    session.commit()

    usage = client.get(
        f"/api/v1/workspaces/{workspace.id}/costs/usage",
        headers=_headers(owner.id),
    )
    foreign_usage = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/costs/usage",
        headers=_headers(other_owner.id),
    )
    summary = client.get(
        f"/api/v1/workspaces/{workspace.id}/costs/summary",
        headers=_headers(owner.id),
        params={"group_by": "model"},
    )

    assert usage.status_code == 200
    assert usage.json()["total"] == 1
    assert usage.json()["items"][0]["metering_status"] == "priced"
    assert foreign_usage.status_code == 200
    assert foreign_usage.json()["total"] == 0
    assert summary.status_code == 200
    assert summary.json()["totals"]["total_tokens"] == 1_500
    assert summary.json()["totals"]["total_cost"] == "0.017000000000"
    actions = set(
        session.scalars(
            select(AuditEvent.action).where(AuditEvent.workspace_id == workspace.id)
        ).all()
    )
    assert {"cost.pricing_rule_created", "cost.budget_updated"} <= actions


def test_cost_api_rejects_invalid_configuration_inputs() -> None:
    client, session = _client()
    owner, workspace = _seed_workspace(session, "cost-validation", "cost-validation")
    headers = _headers(owner.id)
    now = datetime.now(UTC)

    blank_provider = client.post(
        f"/api/v1/workspaces/{workspace.id}/costs/pricing-rules",
        headers=headers,
        json={
            "provider": "   ",
            "model": "gpt-cost",
            "version": "v1",
            "currency": "USD",
            "input_rate_per_million": "1",
            "output_rate_per_million": "1",
            "effective_from": now.isoformat(),
        },
    )
    blank_model = client.post(
        f"/api/v1/workspaces/{workspace.id}/costs/pricing-rules",
        headers=headers,
        json={
            "provider": "openai",
            "model": "   ",
            "version": "v1",
            "currency": "USD",
            "input_rate_per_million": "1",
            "output_rate_per_million": "1",
            "effective_from": now.isoformat(),
        },
    )
    invalid_currency = client.put(
        f"/api/v1/workspaces/{workspace.id}/costs/budget",
        headers=headers,
        json={
            "currency": "US1",
            "monthly_limit": "100",
            "warning_ratio": "0.8",
            "enforcement": "warn",
        },
    )
    invalid_query_currency = client.get(
        f"/api/v1/workspaces/{workspace.id}/costs/summary",
        headers=headers,
        params={"currency": "US1"},
    )

    assert blank_provider.status_code == 400
    assert blank_provider.json()["error"]["message"] == "provider must not be blank"
    assert blank_model.status_code == 400
    assert blank_model.json()["error"]["message"] == "model must not be blank"
    assert invalid_currency.status_code == 422
    assert invalid_query_currency.status_code == 422


def _client() -> tuple[TestClient, Session]:
    _patch_portable_types_for_sqlite()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    app = create_app(
        Settings(
            environment="test",
            log_format="text",
            internal_api_token=TOKEN,
            database_url="sqlite+pysqlite:///:memory:",
        )
    )

    def override_db_session() -> Generator[Session, None, None]:
        request_session = session_factory()
        try:
            yield request_session
        finally:
            request_session.close()

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_settings] = lambda: app.state.settings
    return TestClient(app), session


def _seed_workspace(session: Session, name: str, slug: str) -> tuple[User, Workspace]:
    user = User(email=f"{uuid4()}@example.com", display_name=name)
    workspace = Workspace(owner=user, name=name, slug=f"{slug}-{uuid4()}", settings={})
    session.add_all(
        [
            user,
            workspace,
            WorkspaceMember(workspace=workspace, user=user, role="owner"),
        ]
    )
    session.commit()
    return user, workspace


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user_id)}


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
