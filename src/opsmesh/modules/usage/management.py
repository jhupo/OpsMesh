"""Authorized price, budget and invoice management, without accounting write access."""

from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation
from opsmesh.modules.models.api import ModelProfiles

from .api import (
    BudgetLimit,
    BudgetStatus,
    CostRecord,
    ExchangeRateVersion,
    InvoiceReconciliation,
    PriceVersion,
    ProviderInvoiceLine,
    ResourcePriceVersion,
    UsageService,
)


class UsageManagement:
    def __init__(
        self,
        service: UsageService,
        accounts: AccountDirectory,
        models: ModelProfiles,
        audit: AuditJournal,
    ) -> None:
        self.service, self.accounts, self.models, self.audit = service, accounts, models, audit

    async def _authorize(self, actor_id: str, resource: str, *, manage: bool = False) -> None:
        principal = await self.accounts.get_principal(actor_id)
        if manage or resource != actor_id:
            await self.accounts.authorize(
                principal, "usage:manage" if manage else "usage:read", resource
            )
        elif not principal.active:
            await self.accounts.authorize(principal, "usage:read", resource)

    async def costs(
        self, principal_id: str, run_id: str | None = None, *, actor_id: str
    ) -> tuple[CostRecord, ...]:
        await self._authorize(actor_id, principal_id)
        return await self.service.costs(principal_id, run_id)

    async def budget(self, principal_id: str, currency: str, *, actor_id: str) -> BudgetStatus:
        await self._authorize(actor_id, principal_id)
        return await self.service.budget(principal_id, currency)

    async def list_prices(self, *, actor_id: str) -> tuple[PriceVersion, ...]:
        await self._authorize(actor_id, "prices")
        return await self.service.list_prices()

    async def list_resource_prices(self, *, actor_id: str) -> tuple[ResourcePriceVersion, ...]:
        await self._authorize(actor_id, "prices")
        return await self.service.list_resource_prices()

    async def list_exchange_rates(self, *, actor_id: str) -> tuple[ExchangeRateVersion, ...]:
        await self._authorize(actor_id, "prices")
        return await self.service.list_exchange_rates()

    async def list_reconciliations(
        self, principal_id: str, *, actor_id: str
    ) -> tuple[InvoiceReconciliation, ...]:
        await self._authorize(actor_id, principal_id)
        return await self.service.list_reconciliations(principal_id)

    async def publish_price(self, price: PriceVersion, *, actor_id: str) -> PriceVersion:
        await self._authorize(actor_id, "prices", manage=True)
        await self.models.get_definition(price.model_profile)
        async with audited_operation(self.audit, "usage.publish_price", price.id, actor_id):
            return await self.service.publish_price(price)

    async def publish_resource_price(
        self, price: ResourcePriceVersion, *, actor_id: str
    ) -> ResourcePriceVersion:
        await self._authorize(actor_id, "prices", manage=True)
        async with audited_operation(
            self.audit, "usage.publish_resource_price", price.id, actor_id
        ):
            return await self.service.publish_resource_price(price)

    async def publish_exchange_rate(
        self, rate: ExchangeRateVersion, *, actor_id: str
    ) -> ExchangeRateVersion:
        await self._authorize(actor_id, "prices", manage=True)
        async with audited_operation(self.audit, "usage.publish_exchange_rate", rate.id, actor_id):
            return await self.service.publish_exchange_rate(rate)

    async def set_budget(self, limit: BudgetLimit, *, actor_id: str) -> BudgetStatus:
        await self._authorize(actor_id, limit.principal_id, manage=True)
        await self.accounts.get_principal(limit.principal_id)
        async with audited_operation(self.audit, "usage.set_budget", limit.principal_id, actor_id):
            return await self.service.set_budget(limit)

    async def reprice(self, principal_id: str, *, actor_id: str) -> tuple[CostRecord, ...]:
        await self._authorize(actor_id, principal_id, manage=True)
        async with audited_operation(self.audit, "usage.reprice", principal_id, actor_id):
            return await self.service.reprice(principal_id)

    async def reconcile(self, line: ProviderInvoiceLine, *, actor_id: str) -> InvoiceReconciliation:
        await self._authorize(actor_id, line.principal_id, manage=True)
        async with audited_operation(self.audit, "usage.reconcile", line.invoice_id, actor_id):
            return await self.service.reconcile(line)
