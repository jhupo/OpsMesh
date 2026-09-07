from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.dialects.sqlite import JSON as SqliteJSON
from sqlalchemy.orm import Session, sessionmaker

from backend.app.agent_runtime.contracts import (
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeContext,
    AgentRuntimeEvent,
)
from backend.app.agents.models import AgentProfile
from backend.app.costs.service import CostAccountingService, CostBudgetExceededError
from backend.app.costs.usage import normalize_model_usage
from backend.app.db import models as registered_models  # noqa: F401
from backend.app.db.base import Base
from backend.app.identity.models import User
from backend.app.runs.models import AgentRun
from backend.app.workspaces.models import Workspace, WorkspaceMember


def test_usage_normalization_handles_provider_aliases_and_redacts_raw_payload() -> None:
    result = AgentRunResult(
        final_output="done",
        events=(
            AgentRuntimeEvent(
                event_type="model.usage",
                payload={
                    "usage": {
                        "prompt_tokens": 1_000,
                        "completion_tokens": 500,
                        "total_tokens": 1_500,
                        "input_tokens_details": {"cached_tokens": 200},
                        "output_tokens_details": {"reasoning_tokens": 100},
                        "api_key": "sk-cost-secret",
                    }
                },
            ),
        ),
    )

    usage = normalize_model_usage(result)

    assert usage.available is True
    assert usage.request_count == 1
    assert usage.input_tokens == 1_000
    assert usage.output_tokens == 500
    assert usage.cached_input_tokens == 200
    assert usage.reasoning_tokens == 100
    assert usage.total_tokens == 1_500
    assert usage.raw_usage["api_key"] == "[redacted]"
    assert "sk-cost-secret" not in str(usage.raw_usage)


def test_usage_normalization_aggregates_multiple_model_calls() -> None:
    result = AgentRunResult(
        final_output="done",
        events=(
            AgentRuntimeEvent(
                event_type="model.usage",
                payload={
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cache_read_input_tokens": 10,
                    }
                },
            ),
            AgentRuntimeEvent(
                event_type="model.usage",
                payload={
                    "usage": {
                        "input_tokens": 50,
                        "output_tokens": 30,
                        "reasoning_tokens": 5,
                    }
                },
            ),
        ),
    )

    usage = normalize_model_usage(result)

    assert usage.available is True
    assert usage.request_count == 2
    assert usage.input_tokens == 150
    assert usage.output_tokens == 50
    assert usage.cached_input_tokens == 10
    assert usage.reasoning_tokens == 5
    assert usage.total_tokens == 200
    assert usage.raw_usage == {
        "calls": [
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 10,
            },
            {"input_tokens": 50, "output_tokens": 30, "reasoning_tokens": 5},
        ]
    }


def test_cost_accounting_prices_usage_idempotently_and_isolates_workspaces() -> None:
    session = _session()
    user, workspace, profile, run = _seed_run(session, slug="cost-a")
    _, other_workspace, _, _ = _seed_run(session, slug="cost-b")
    now = datetime.now(UTC)
    service = CostAccountingService(session)
    service.create_pricing_rule(
        workspace_id=workspace.id,
        actor_user_id=user.id,
        provider="openai",
        model="gpt-cost",
        version="2026-09-01",
        currency="usd",
        input_rate_per_million=Decimal("2"),
        output_rate_per_million=Decimal("10"),
        cached_input_rate_per_million=Decimal("1"),
        request_rate=Decimal("0.01"),
        effective_from=now - timedelta(days=1),
        effective_to=None,
        source="contract-2026-09",
    )
    request = _request(profile, run)
    result = _usage_result()

    first = service.record_usage(
        run=run,
        request=request,
        result=result,
        job_attempt=2,
        occurred_at=now,
    )
    second = service.record_usage(
        run=run,
        request=request,
        result=result,
        job_attempt=2,
        occurred_at=now,
    )
    session.commit()

    assert second.id == first.id
    assert first.metering_status == "priced"
    assert first.pricing_version == "2026-09-01"
    assert first.currency == "USD"
    assert first.input_cost == Decimal("0.001600000000")
    assert first.cached_input_cost == Decimal("0.000200000000")
    assert first.output_cost == Decimal("0.005000000000")
    assert first.request_cost == Decimal("0.010000000000")
    assert first.total_cost == Decimal("0.016800000000")

    own_rows, own_total = service.list_usage(
        workspace.id,
        start_at=now - timedelta(days=1),
        end_at=now + timedelta(days=1),
        provider=None,
        model=None,
        limit=100,
        offset=0,
    )
    foreign_rows, foreign_total = service.list_usage(
        other_workspace.id,
        start_at=now - timedelta(days=1),
        end_at=now + timedelta(days=1),
        provider=None,
        model=None,
        limit=100,
        offset=0,
    )
    assert [row.id for row in own_rows] == [first.id]
    assert own_total == 1
    assert foreign_rows == []
    assert foreign_total == 0


def test_cost_summary_reports_unpriced_usage_and_enforces_block_budget() -> None:
    session = _session()
    user, workspace, profile, run = _seed_run(session, slug="budget")
    now = datetime.now(UTC)
    service = CostAccountingService(session)
    service.create_pricing_rule(
        workspace_id=workspace.id,
        actor_user_id=user.id,
        provider="openai",
        model="*",
        version="wildcard-v1",
        currency="USD",
        input_rate_per_million=Decimal("2"),
        output_rate_per_million=Decimal("10"),
        cached_input_rate_per_million=None,
        request_rate=Decimal("0.01"),
        effective_from=now - timedelta(days=1),
        effective_to=None,
        source="operator",
    )
    priced = service.record_usage(
        run=run,
        request=_request(profile, run),
        result=_usage_result(),
        job_attempt=0,
        occurred_at=now,
    )
    unpriced_profile, unpriced_run = _add_run(session, workspace, model="other-model")
    unpriced = service.record_usage(
        run=unpriced_run,
        request=AgentRunRequest(
            agent_profile=unpriced_profile,
            input_text="test",
            context=AgentRuntimeContext(
                workspace_id=workspace.id,
                task_id=None,
                run_id=unpriced_run.id,
            ),
            provider="anthropic",
            model="other-model",
        ),
        result=_usage_result(),
        job_attempt=0,
        occurred_at=now,
    )
    missing_profile, missing_run = _add_run(session, workspace, model="missing-usage")
    missing = service.record_usage(
        run=missing_run,
        request=AgentRunRequest(
            agent_profile=missing_profile,
            input_text="test",
            context=AgentRuntimeContext(
                workspace_id=workspace.id,
                task_id=None,
                run_id=missing_run.id,
            ),
            provider="anthropic",
            model="missing-usage",
        ),
        result=AgentRunResult(final_output="no usage"),
        job_attempt=0,
        occurred_at=now,
    )
    service.create_pricing_rule(
        workspace_id=workspace.id,
        actor_user_id=user.id,
        provider="google",
        model="gemini-cost",
        version="eur-v1",
        currency="EUR",
        input_rate_per_million=Decimal("1"),
        output_rate_per_million=Decimal("1"),
        cached_input_rate_per_million=None,
        request_rate=Decimal("0"),
        effective_from=now - timedelta(days=1),
        effective_to=None,
        source="test",
    )
    euro_profile, euro_run = _add_run(session, workspace, model="gemini-cost")
    euro_usage = service.record_usage(
        run=euro_run,
        request=AgentRunRequest(
            agent_profile=euro_profile,
            input_text="test",
            context=AgentRuntimeContext(
                workspace_id=workspace.id,
                task_id=None,
                run_id=euro_run.id,
            ),
            provider="google",
            model="gemini-cost",
        ),
        result=_usage_result(),
        job_attempt=0,
        occurred_at=now,
    )
    euro_missing_profile, euro_missing_run = _add_run(
        session,
        workspace,
        model="gemini-cost",
    )
    euro_missing_usage = service.record_usage(
        run=euro_missing_run,
        request=AgentRunRequest(
            agent_profile=euro_missing_profile,
            input_text="test",
            context=AgentRuntimeContext(
                workspace_id=workspace.id,
                task_id=None,
                run_id=euro_missing_run.id,
            ),
            provider="google",
            model="gemini-cost",
        ),
        result=AgentRunResult(final_output="no usage"),
        job_attempt=0,
        occurred_at=now,
    )
    service.upsert_budget(
        workspace_id=workspace.id,
        actor_user_id=user.id,
        currency="USD",
        monthly_limit=Decimal("0.01"),
        warning_ratio=Decimal("0.8"),
        enforcement="block",
        enabled=True,
    )
    service.upsert_budget(
        workspace_id=workspace.id,
        actor_user_id=user.id,
        currency="EUR",
        monthly_limit=Decimal("100"),
        warning_ratio=Decimal("0.8"),
        enforcement="block",
        enabled=True,
    )
    session.commit()

    assert unpriced.metering_status == "unpriced"
    assert missing.metering_status == "missing_usage"
    assert missing.request_count == 1
    assert euro_usage.currency == "EUR"
    assert euro_missing_usage.currency == "EUR"
    assert euro_missing_usage.metering_status == "missing_usage"

    summary = service.summary(
        workspace.id,
        start_at=now - timedelta(days=1),
        end_at=now + timedelta(days=1),
        currency="USD",
        group_by="provider",
    )
    assert summary["totals"] == {
        "records": 3,
        "priced_records": 1,
        "unpriced_records": 2,
        "request_count": 3,
        "input_tokens": 2_000,
        "output_tokens": 1_000,
        "cached_input_tokens": 400,
        "reasoning_tokens": 200,
        "total_tokens": 3_000,
        "total_cost": priced.total_cost,
    }
    budget = service.budget_status(workspace.id, currency="USD", now=now)
    assert budget.state == "exhausted"
    assert budget.unpriced_records == 2
    with pytest.raises(CostBudgetExceededError, match="budget exhausted"):
        service.assert_budget_available(
            workspace.id,
            provider="openai",
            model="gpt-cost",
            now=now,
        )
    service.assert_budget_available(
        workspace.id,
        provider="google",
        model="gemini-cost",
        now=now,
    )
    with pytest.raises(CostBudgetExceededError, match="requires an active model pricing rule"):
        service.assert_budget_available(
            workspace.id,
            provider="anthropic",
            model="unpriced-model",
            now=now,
        )


def test_cost_configuration_rejects_invalid_service_inputs() -> None:
    session = _session()
    user, workspace, _, _ = _seed_run(session, slug="cost-validation")
    service = CostAccountingService(session)
    pricing = {
        "workspace_id": workspace.id,
        "actor_user_id": user.id,
        "provider": "openai",
        "model": "gpt-cost",
        "version": "v1",
        "currency": "USD",
        "input_rate_per_million": Decimal("1"),
        "output_rate_per_million": Decimal("1"),
        "cached_input_rate_per_million": None,
        "request_rate": Decimal("0"),
        "effective_from": datetime.now(UTC),
        "effective_to": None,
        "source": "test",
    }

    with pytest.raises(ValueError, match="provider must not be blank"):
        service.create_pricing_rule(**{**pricing, "provider": "   "})
    with pytest.raises(ValueError, match="model must not be blank"):
        service.create_pricing_rule(**{**pricing, "model": "   "})
    with pytest.raises(ValueError, match="version must not be blank"):
        service.create_pricing_rule(**{**pricing, "version": "   "})
    with pytest.raises(ValueError, match="three-letter ASCII"):
        service.create_pricing_rule(**{**pricing, "currency": "US1"})
    with pytest.raises(ValueError, match="monthly_limit"):
        service.upsert_budget(
            workspace_id=workspace.id,
            actor_user_id=user.id,
            currency="USD",
            monthly_limit=Decimal("0"),
            warning_ratio=Decimal("0.8"),
            enforcement="warn",
            enabled=True,
        )
    with pytest.raises(ValueError, match="warning_ratio"):
        service.upsert_budget(
            workspace_id=workspace.id,
            actor_user_id=user.id,
            currency="USD",
            monthly_limit=Decimal("1"),
            warning_ratio=Decimal("1.1"),
            enforcement="warn",
            enabled=True,
        )


def _usage_result() -> AgentRunResult:
    return AgentRunResult(
        final_output="done",
        events=(
            AgentRuntimeEvent(
                event_type="model.usage",
                payload={
                    "usage": {
                        "input_tokens": 1_000,
                        "output_tokens": 500,
                        "total_tokens": 1_500,
                        "input_tokens_details": {"cached_tokens": 200},
                        "output_tokens_details": {"reasoning_tokens": 100},
                    }
                },
            ),
        ),
    )


def _request(profile: AgentProfile, run: AgentRun) -> AgentRunRequest:
    return AgentRunRequest(
        agent_profile=profile,
        input_text="test",
        context=AgentRuntimeContext(
            workspace_id=run.workspace_id,
            task_id=None,
            run_id=run.id,
        ),
        provider="openai",
        model="gpt-cost",
    )


def _session() -> Session:
    _patch_portable_types_for_sqlite()
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed_run(
    session: Session,
    *,
    slug: str,
) -> tuple[User, Workspace, AgentProfile, AgentRun]:
    user = User(email=f"{slug}-{uuid4()}@example.com", display_name="Owner")
    workspace = Workspace(owner=user, name=slug, slug=f"{slug}-{uuid4()}", settings={})
    session.add_all(
        [
            user,
            workspace,
            WorkspaceMember(workspace=workspace, user=user, role="owner"),
        ]
    )
    session.flush()
    profile, run = _add_run(session, workspace, model="gpt-cost")
    session.commit()
    return user, workspace, profile, run


def _add_run(
    session: Session,
    workspace: Workspace,
    *,
    model: str,
) -> tuple[AgentProfile, AgentRun]:
    profile = AgentProfile(
        workspace_id=workspace.id,
        name=f"Agent {model}",
        role="operator",
        model=model,
    )
    session.add(profile)
    session.flush()
    run = AgentRun(
        workspace_id=workspace.id,
        agent_profile_id=profile.id,
        model=model,
        input={},
    )
    session.add(run)
    session.flush()
    return profile, run


def _patch_portable_types_for_sqlite() -> None:
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PostgresUUID):
                column.type = column.type.as_generic()
            if isinstance(column.type, JSONB):
                column.type = SqliteJSON()
