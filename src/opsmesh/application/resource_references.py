"""Cross-domain reference inspection at the composition boundary."""

from typing import Any

from sqlalchemy import select

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.infrastructure.database.transactions import database_connection
from opsmesh.modules.accounts.api import AccountDirectory, UserPrincipal
from opsmesh.modules.agents.api import AgentDefinitionCatalog
from opsmesh.modules.resource_contracts import ResourceReference
from opsmesh.modules.runtime.api import AgentRuntime, AgentRunView
from opsmesh.modules.runtime.queries import iter_runs


class PlatformResourceReferences:
    def __init__(
        self,
        *,
        accounts: AccountDirectory,
        agents: AgentDefinitionCatalog,
        runtime: AgentRuntime,
        database: AsyncDatabase,
    ) -> None:
        self.accounts = accounts
        self.agents = agents
        self.runtime = runtime
        self.database = database

    async def lookup(
        self, actor_id: str, kind: str, reference: str
    ) -> tuple[ResourceReference, ...]:
        result: list[ResourceReference] = []
        keys = {
            "model": {"model_profile_ref"},
            "tool": {"tool_ref"},
            "skill": {"skill_refs"},
            "mcp": {"mcp_refs"},
            "script": {"tool_ref", "resource_ref"},
        }.get(kind, {kind})
        principal = await self.accounts.get_principal(actor_id)
        for definition in await self.agents.list():
            try:
                await self.accounts.authorize(
                    principal, "agent:read", definition.id, owner_id=definition.owner_id
                )
            except OpsMeshError as error:
                if error.code not in {"FORBIDDEN", "AUTHORIZATION_DENIED"}:
                    raise
                continue
            data = {
                "draft": definition.draft.model_dump(),
                "versions": {
                    str(spec.revision): spec.model_dump()
                    for spec in await self.agents.revisions(definition.id)
                },
            }
            for path, value in walk_fields(data):
                key = path.rsplit(".", 1)[-1]
                values = value if isinstance(value, (list, tuple)) else [value]
                if key in keys and any(
                    isinstance(item, str)
                    and (item == reference or item.split("@", 1)[0] == reference)
                    for item in values
                ):
                    result.append(
                        ResourceReference(kind="agent", reference=definition.id, field=path)
                    )
        async for run in iter_runs(
            self.runtime,
            statuses=(
                "queued",
                "preparing",
                "running",
                "waiting_approval",
                "resuming",
                "finalizing",
            ),
        ):
            result.extend(await self._run_references(principal, run, reference, keys))
        return tuple(result)

    async def _run_references(
        self, principal: UserPrincipal, run: AgentRunView, reference: str, keys: set[str]
    ) -> list[ResourceReference]:
        result: list[ResourceReference] = []
        try:
            await self.accounts.authorize(principal, "run:read", run.id, owner_id=run.principal_id)
        except OpsMeshError as error:
            if error.code not in {"FORBIDDEN", "AUTHORIZATION_DENIED"}:
                raise
            return []
        for path, value in walk_fields(run.agent.model_dump()):
            values = value if isinstance(value, (list, tuple)) else [value]
            if path.rsplit(".", 1)[-1] in keys and any(
                isinstance(value, str)
                and (value == reference or value.split("@", 1)[0] == reference)
                for value in values
            ):
                result.append(ResourceReference(kind="run", reference=run.id, field=path))
        return result

    async def snapshots(self) -> set[str]:
        # Retain snapshots referenced by any persisted definition, checkpoint or sandbox binding.
        references: set[str] = set()
        database = self.database
        async with database_connection(database.engine) as connection:
            for table in database.metadata.sorted_tables:
                if "data" not in table.c or "audit" in table.name:
                    continue
                for row in await connection.execute(select(table.c.data)):
                    references.update(snapshot_references(row[0]))
        return references


def snapshot_references(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                key in {"snapshot_ref", "snapshot_id", "workspace_snapshot_id"}
                or (key == "restore_snapshot_id" and value.get("restore_state") != "ready")
            ) and isinstance(child, str):
                references.add(child)
            references.update(snapshot_references(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            references.update(snapshot_references(child))
    return references


def walk_fields(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    result: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.append((path, child))
            result.extend(walk_fields(child, path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            result.extend(walk_fields(child, f"{prefix}.{index}"))
    return result
