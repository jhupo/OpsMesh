"""Read durable usage and manage versioned prices and budgets."""

from fastapi import APIRouter, Request

from opsmesh.modules.usage.api import (
    BudgetLimit,
    BudgetStatus,
    CostRecord,
    ExchangeRateVersion,
    InvoiceReconciliation,
    PriceVersion,
    ProviderInvoiceLine,
    ResourcePriceVersion,
)

from ..security import CurrentUser, authorize, get_application

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/costs")
async def costs(
    request: Request, user: CurrentUser, principal_id: str | None = None, run_id: str | None = None
) -> tuple[CostRecord, ...]:
    selected = principal_id or user.id
    if selected != user.id:
        await authorize(request, user, "usage:read", selected)
    return await get_application(request).services.usage_management.costs(
        selected, run_id, actor_id=user.id
    )


@router.get("/budget")
async def budget(
    request: Request, user: CurrentUser, currency: str = "USD", principal_id: str | None = None
) -> BudgetStatus:
    selected = principal_id or user.id
    if selected != user.id:
        await authorize(request, user, "usage:read", selected)
    return await get_application(request).services.usage_management.budget(
        selected, currency, actor_id=user.id
    )


@router.put("/budget")
async def set_budget(body: BudgetLimit, request: Request, user: CurrentUser) -> BudgetStatus:
    await authorize(request, user, "usage:manage", body.principal_id)
    await get_application(request).services.accounts.get_principal(body.principal_id)
    return await get_application(request).services.usage_management.set_budget(
        body, actor_id=user.id
    )


@router.post("/prices", status_code=201)
async def publish_price(body: PriceVersion, request: Request, user: CurrentUser) -> PriceVersion:
    await authorize(request, user, "usage:manage", "prices")
    return await get_application(request).services.usage_management.publish_price(
        body, actor_id=user.id
    )


@router.post("/resource-prices", status_code=201)
async def publish_resource_price(
    body: ResourcePriceVersion, request: Request, user: CurrentUser
) -> ResourcePriceVersion:
    await authorize(request, user, "usage:manage", "prices")
    return await get_application(request).services.usage_management.publish_resource_price(
        body, actor_id=user.id
    )


@router.post("/exchange-rates", status_code=201)
async def publish_exchange_rate(
    body: ExchangeRateVersion, request: Request, user: CurrentUser
) -> ExchangeRateVersion:
    await authorize(request, user, "usage:manage", "prices")
    return await get_application(request).services.usage_management.publish_exchange_rate(
        body, actor_id=user.id
    )


@router.post("/reprice/{principal_id}")
async def reprice(principal_id: str, request: Request, user: CurrentUser) -> tuple[CostRecord, ...]:
    await authorize(request, user, "usage:manage", principal_id)
    return await get_application(request).services.usage_management.reprice(
        principal_id, actor_id=user.id
    )


@router.post("/reconcile")
async def reconcile(
    body: ProviderInvoiceLine, request: Request, user: CurrentUser
) -> InvoiceReconciliation:
    await authorize(request, user, "usage:manage", body.principal_id)
    return await get_application(request).services.usage_management.reconcile(
        body, actor_id=user.id
    )


@router.get("/prices")
async def prices(request: Request, user: CurrentUser) -> tuple[PriceVersion, ...]:
    return await get_application(request).services.usage_management.list_prices(actor_id=user.id)


@router.get("/resource-prices")
async def resource_prices(request: Request, user: CurrentUser) -> tuple[ResourcePriceVersion, ...]:
    return await get_application(request).services.usage_management.list_resource_prices(
        actor_id=user.id
    )


@router.get("/exchange-rates")
async def exchange_rates(request: Request, user: CurrentUser) -> tuple[ExchangeRateVersion, ...]:
    return await get_application(request).services.usage_management.list_exchange_rates(
        actor_id=user.id
    )


@router.get("/reconciliations")
async def reconciliations(
    request: Request, user: CurrentUser, principal_id: str | None = None
) -> tuple[InvoiceReconciliation, ...]:
    return await get_application(request).services.usage_management.list_reconciliations(
        principal_id or user.id, actor_id=user.id
    )
