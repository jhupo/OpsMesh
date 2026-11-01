"""Atomic publication of immutable agent revisions."""

from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase, StoredRecord, VersionedRecordStore
from opsmesh.modules.agents.api import AgentDefinition, AgentPreparation, AgentValidator


class PersistentAgentDefinitionCatalog:
    def __init__(
        self,
        database: AsyncDatabase,
        validate: AgentValidator,
        prepare: AgentPreparation | None = None,
    ) -> None:
        self._store = VersionedRecordStore(database, "agents_definitions")
        self._validate = validate
        self._prepare = prepare

    @staticmethod
    def _view(record: StoredRecord) -> AgentDefinition:
        return AgentDefinition(
            id=record.id,
            owner_id=record.data["owner_id"],
            draft=AgentSpecification.model_validate(record.data["draft"]),
            published_revision=record.data["published_revision"],
            enabled=record.data["enabled"],
            revision=record.revision,
        )

    async def _record(self, agent_id: str) -> StoredRecord:
        record = await self._store.get(agent_id)
        if record is None:
            raise OpsMeshError("NOT_FOUND", "Agent definition does not exist")
        return record

    async def create(self, specification: AgentSpecification, owner_id: str) -> AgentDefinition:
        if "@" in specification.id:
            raise OpsMeshError("CONFIG_INVALID", "Agent identifier cannot contain @")
        record = await self._store.create(
            specification.id,
            {
                "owner_id": owner_id,
                "draft": specification.model_dump(mode="json"),
                "published_revision": None,
                "enabled": True,
                "versions": {},
            },
        )
        return self._view(record)

    async def get(self, agent_id: str) -> AgentDefinition:
        return self._view(await self._record(agent_id))

    async def list(self) -> list[AgentDefinition]:
        return [self._view(record) for record in await self._store.list()]

    async def revisions(self, agent_id: str) -> tuple[AgentSpecification, ...]:
        record = await self._record(agent_id)
        return tuple(
            AgentSpecification.model_validate(value) for value in record.data["versions"].values()
        )

    async def update(
        self, agent_id: str, specification: AgentSpecification, expected_revision: int
    ) -> AgentDefinition:
        if agent_id != specification.id:
            raise OpsMeshError("CONFIG_INVALID", "Agent identifier cannot change")
        record = await self._record(agent_id)
        return self._view(
            await self._store.update(
                agent_id,
                {**record.data, "draft": specification.model_dump(mode="json")},
                expected_revision,
            )
        )

    async def set_enabled(
        self, agent_id: str, enabled: bool, expected_revision: int
    ) -> AgentDefinition:
        record = await self._record(agent_id)
        return self._view(
            await self._store.update(
                agent_id,
                {**record.data, "enabled": enabled},
                expected_revision,
                events=[
                    {
                        "type": "agents.availability.changed",
                        "source": "agents",
                        "payload": {"agent_id": agent_id, "enabled": enabled},
                    }
                ],
            )
        )

    async def publish(self, agent_id: str, expected_revision: int) -> AgentSpecification:
        record = await self._record(agent_id)
        specification = AgentSpecification.model_validate(record.data["draft"])
        if self._prepare:
            specification = await self._prepare(specification, record.data["owner_id"])
        revision = int(record.data["published_revision"] or 0) + 1
        specification = specification.model_copy(update={"revision": revision})
        await self._validate(specification)
        versions = {**record.data["versions"], str(revision): specification.model_dump(mode="json")}
        await self._store.update(
            agent_id,
            {**record.data, "versions": versions, "published_revision": revision},
            expected_revision,
            events=[
                {
                    "type": "agents.revision.published",
                    "source": "agents",
                    "payload": {"agent_ref": specification.ref},
                }
            ],
        )
        return specification

    async def get_revision(self, reference: str) -> AgentSpecification:
        agent_id, separator, version = reference.rpartition("@")
        record = await self._record(agent_id if separator else reference)
        if not record.data["enabled"]:
            raise OpsMeshError("FORBIDDEN", "Agent is disabled")
        version = version if separator else str(record.data["published_revision"])
        try:
            return AgentSpecification.model_validate(record.data["versions"][version])
        except KeyError as error:
            raise OpsMeshError("NOT_FOUND", "Published agent revision does not exist") from error

    async def clone(self, reference: str, target_id: str, owner_id: str) -> AgentDefinition:
        specification = await self.get_revision(reference)
        clone = specification.model_copy(deep=True, update={"id": target_id, "revision": 1})
        return await self.create(clone, owner_id)
