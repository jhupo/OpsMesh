"""Protected configuration management; runtime reads use the internal service."""

from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation

from .api import ConfigurationEntry, PlatformConfiguration


def validate_configuration_values(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in {
                "password",
                "password_hash",
                "api_key",
                "access_token",
                "secret",
                "authorization",
            }:
                raise OpsMeshError(
                    "CONFIG_INVALID", "Configuration must reference deployed secrets"
                )
            validate_configuration_values(nested)
    elif isinstance(value, (tuple, list)):
        for nested in value:
            validate_configuration_values(nested)


class ConfigurationManagement:
    def __init__(
        self, service: PlatformConfiguration, accounts: AccountDirectory, audit: AuditJournal
    ) -> None:
        self.service, self.accounts, self.audit = service, accounts, audit

    async def _authorize(self, actor_id: str, action: str, key: str) -> None:
        await self.accounts.authorize(await self.accounts.get_principal(actor_id), action, key)

    async def get(self, key: str, *, actor_id: str) -> ConfigurationEntry | None:
        await self._authorize(actor_id, "configuration:read", key)
        return await self.service.get(key)

    async def list(self, *, actor_id: str) -> list[ConfigurationEntry]:
        await self._authorize(actor_id, "configuration:read", "*")
        return await self.service.list()

    async def set(
        self,
        key: str,
        value: dict[str, Any],
        expected_revision: int | None = None,
        *,
        actor_id: str,
    ) -> ConfigurationEntry:
        await self._authorize(actor_id, "configuration:manage", key)
        validate_configuration_values(value)
        async with audited_operation(
            self.audit,
            "configuration.set",
            key,
            actor_id,
            details={"expected_revision": expected_revision},
        ):
            return await self.service.set(key, value, expected_revision)
