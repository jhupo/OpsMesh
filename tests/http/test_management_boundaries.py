"""Public management dependencies and HTTP share identity, audit and cost rules."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from opsmesh import dependencies
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.models.api import ModelProfile
from opsmesh.modules.usage.api import (
    ExchangeRateVersion,
    PriceVersion,
    ProviderInvoiceLine,
    UsageFact,
)


async def test_global_and_system_audit_queries_are_explicit_and_authorized(http_platform):
    app, client, headers, principals = http_platform
    await app.services.audit.record("system.probe", "worker", outcome="recorded")
    await app.services.audit.record("personal.probe", "object", actor_id=principals["alice"])
    own = await client.get("/api/v1/audit", headers=headers["alice"])
    assert own.status_code == 200
    assert all(item["actor_id"] == principals["alice"] for item in own.json())
    denied = await client.get("/api/v1/audit?scope=system", headers=headers["alice"])
    assert denied.status_code == 403
    system = await client.get(
        "/api/v1/audit?scope=system&action=system.probe", headers=headers["admin"]
    )
    assert system.status_code == 200
    assert len(system.json()) == 1 and system.json()[0]["actor_id"] is None
    with pytest.raises(OpsMeshError) as failure:
        await dependencies.get_audit().query(actor_id=principals["alice"], scope="all")
    assert failure.value.code == "FORBIDDEN"


async def test_direct_dependency_agent_mutation_records_actor_and_rejects_other_owner(
    http_platform,
):
    app, _, _, principals = http_platform
    management = dependencies.get_agents()
    definition = await management.create(
        AgentSpecification(id="direct", name="Direct", backend="http_test"),
        actor_id=principals["alice"],
    )
    with pytest.raises(OpsMeshError) as failure:
        await management.update(
            "direct", definition.draft, definition.revision, actor_id=principals["bob"]
        )
    assert failure.value.code == "FORBIDDEN"
    assert await management.list(actor_id=principals["bob"]) == []
    evidence = await app.services.audit.query(
        actor_id=principals["alice"], action="agent.create", outcome="succeeded"
    )
    assert len(evidence) == 1 and evidence[0].resource == "direct"
    with pytest.raises(OpsMeshError):
        await dependencies.get_configuration().set(
            "unsafe", {"value": 1}, actor_id=principals["alice"]
        )


async def test_price_exchange_and_reconciliation_queries_survive_management_boundary(http_platform):
    app, client, headers, principals = http_platform
    admin = principals["admin"]
    await app.services.model_management.create(
        ModelProfile(id="metered", model="offline"), actor_id=admin
    )
    await dependencies.get_usage().publish_price(
        PriceVersion(
            id="p1",
            model_profile="metered@1",
            currency="USD",
            input_per_million=Decimal("1"),
            output_per_million=Decimal("2"),
        ),
        actor_id=admin,
    )
    await dependencies.get_usage().publish_exchange_rate(
        ExchangeRateVersion(id="fx", base_currency="USD", quote_currency="CNY", rate=Decimal("7")),
        actor_id=admin,
    )
    line = ProviderInvoiceLine(
        provider="offline",
        invoice_id="invoice",
        line_id="1",
        principal_id=principals["alice"],
        call_id="not_observed",
        amount=Decimal("1"),
        currency="USD",
    )
    reconciliation = await dependencies.get_usage().reconcile(line, actor_id=admin)
    assert reconciliation.status == "missing_usage"
    own = await client.get("/api/v1/usage/reconciliations", headers=headers["alice"])
    assert own.status_code == 200 and own.json()[0]["line"]["invoice_id"] == "invoice"
    denied = await client.get(
        f"/api/v1/usage/reconciliations?principal_id={principals['alice']}", headers=headers["bob"]
    )
    assert denied.status_code == 403
    for endpoint, identity in (("prices", "p1"), ("exchange-rates", "fx")):
        response = await client.get(f"/api/v1/usage/{endpoint}", headers=headers["admin"])
        assert response.status_code == 200 and response.json()[0]["id"] == identity


@pytest.mark.parametrize("entrypoint", ["http", "dependency"])
async def test_withdrawn_model_allows_historical_pricing_without_reactivation(
    http_platform, entrypoint
):
    app, client, headers, principals = http_platform
    admin, owner = principals["admin"], principals["alice"]
    await app.services.model_management.create(
        ModelProfile(id="withdrawn", model="offline"), actor_id=admin
    )
    await app.services.usage.reserve(owner, "historic-run", Decimal("5"), "USD")
    cost = await app.services.usage.record(
        UsageFact(
            call_id="historic-call",
            principal_id=owner,
            run_id="historic-run",
            reservation_id="historic-run",
            model_profile="withdrawn@1",
            input_tokens=1_000_000,
        )
    )
    assert cost.status == "unpriced"
    await app.services.usage.settle(owner, "historic-run", None)
    await app.services.model_management.set_enabled(
        "withdrawn@1", False, actor_id=admin, expected_revision=0, reason="Retired"
    )
    price = PriceVersion(
        id="late-price",
        model_profile="withdrawn@1",
        currency="USD",
        input_per_million=Decimal("2"),
        output_per_million=Decimal("3"),
        effective_at=datetime.now(UTC) - timedelta(days=1),
    )
    denied = await client.post(
        "/api/v1/usage/prices", json=price.model_dump(mode="json"), headers=headers["alice"]
    )
    assert denied.status_code == 403
    if entrypoint == "http":
        response = await client.post(
            "/api/v1/usage/prices", json=price.model_dump(mode="json"), headers=headers["admin"]
        )
        assert response.status_code == 201, response.text
    else:
        await dependencies.get_usage().publish_price(price, actor_id=admin)
    repriced = await dependencies.get_usage().reprice(owner, actor_id=admin)
    assert len(repriced) == 1 and repriced[0].amount == Decimal("2")
    budget = await dependencies.get_usage().budget(owner, "USD", actor_id=owner)
    assert budget.spent == Decimal("2") and budget.reserved == 0
    with pytest.raises(OpsMeshError) as disabled:
        await app.services.models.get("withdrawn@1")
    assert disabled.value.code == "RESOURCE_DISABLED"
    with pytest.raises(OpsMeshError) as missing:
        await dependencies.get_usage().publish_price(
            price.model_copy(update={"id": "missing-price", "model_profile": "withdrawn@99"}),
            actor_id=admin,
        )
    assert missing.value.code == "NOT_FOUND"
