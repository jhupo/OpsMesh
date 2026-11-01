"""Authorized management and explicit external ingestion of Agent event triggers."""

from __future__ import annotations

from datetime import UTC, datetime

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import OpsMeshEvent
from opsmesh.infrastructure.database import StoredRecord, VersionedRecordStore
from opsmesh.modules.triggers.api import (
    TriggerAgentResolver,
    TriggerAuthorizer,
    TriggerDefinition,
    TriggerDelivery,
    TriggerRunResolver,
    TriggerSecretResolver,
    TriggerSubmissionResolver,
    TriggerSubmitter,
    TriggerTargetValidator,
)
from opsmesh.modules.triggers.events import configuration_event

from .delivery import TriggerDeliveryEngine
from .matching import delivery_identity, event_fingerprint, matches
from .webhook import authenticate_webhook


class PersistentTriggerService:
    def __init__(
        self,
        store: VersionedRecordStore,
        authorize: TriggerAuthorizer,
        agent_resolver: TriggerAgentResolver,
        submitter: TriggerSubmitter,
        secret_resolver: TriggerSecretResolver,
        *,
        validate_target: TriggerTargetValidator | None = None,
        run_resolver: TriggerRunResolver | None = None,
        submission_resolver: TriggerSubmissionResolver | None = None,
    ) -> None:
        self.store, self.authorize, self.agent_resolver = store, authorize, agent_resolver
        self.secret_resolver, self.validate_target = secret_resolver, validate_target
        self.engine = TriggerDeliveryEngine(
            store,
            submitter,
            self._validate_execution,
            run_resolver,
            submission_resolver=submission_resolver,
        )

    async def _prepare(self, definition: TriggerDefinition, actor_id: str) -> TriggerDefinition:
        await self.authorize(actor_id, "triggers.manage", definition.id)
        if actor_id != definition.principal_id:
            await self.authorize(actor_id, "triggers:impersonate", definition.principal_id)
        await self.authorize(definition.principal_id, "triggers.execute", definition.id)
        published = await self.agent_resolver(definition.agent_revision)
        await self.authorize(actor_id, "agent:run", published.ref)
        await self.authorize(definition.principal_id, "agent:run", published.ref)
        definition = definition.model_copy(update={"agent_revision": published.ref})
        if definition.webhook_secret_ref:
            await self.authorize(actor_id, "secret:use", definition.webhook_secret_ref)
            if len(self.secret_resolver(definition.webhook_secret_ref)) < 32:
                raise OpsMeshError(
                    "CONFIG_INVALID", "Webhook signing keys must contain at least 32 characters"
                )
        if self.validate_target is not None:
            await self.validate_target(definition, actor_id)
        elif definition.reply_target or definition.run_overrides.model_dump(exclude_defaults=True):
            raise OpsMeshError("CONFIG_INVALID", "A trigger resource validator is required")
        return definition

    async def _validate_execution(self, definition: TriggerDefinition, actor_id: str) -> None:
        prepared = await self._prepare(definition, actor_id)
        if prepared.agent_revision != definition.agent_revision:
            raise OpsMeshError("CONFLICT", "Trigger Agent revision must remain fixed")

    async def _record(self, trigger_id: str, *, include_deleted: bool = False) -> StoredRecord:
        record = await self.store.get(trigger_id)
        if (
            record is None
            or record.data.get("kind") != "trigger"
            or (record.data.get("deleted") and not include_deleted)
        ):
            raise OpsMeshError("NOT_FOUND", "Event trigger does not exist")
        return record

    @staticmethod
    def _version(record: StoredRecord, expected: int) -> TriggerDefinition:
        definition = TriggerDefinition.model_validate(record.data["definition"])
        if definition.revision != expected:
            raise OpsMeshError("CONFLICT", "Event trigger configuration was changed")
        return definition

    async def create(self, definition: TriggerDefinition, *, actor_id: str) -> TriggerDefinition:
        definition = (await self._prepare(definition, actor_id)).model_copy(update={"revision": 1})
        await self.store.create(
            definition.id,
            {
                "kind": "trigger",
                "definition": definition.model_dump(mode="json"),
                "creator_id": actor_id,
            },
            events=(configuration_event(definition, actor_id, "created"),),
        )
        return definition

    async def get(self, trigger_id: str, *, actor_id: str) -> TriggerDefinition:
        await self.authorize(actor_id, "triggers.read", trigger_id)
        return TriggerDefinition.model_validate((await self._record(trigger_id)).data["definition"])

    async def list_triggers(self, *, actor_id: str) -> list[TriggerDefinition]:
        await self.authorize(actor_id, "triggers.read", "*")
        return [
            TriggerDefinition.model_validate(r.data["definition"])
            for r in await self.store.list()
            if r.data.get("kind") == "trigger" and not r.data.get("deleted")
        ]

    async def update(
        self,
        trigger_id: str,
        definition: TriggerDefinition,
        expected_revision: int,
        *,
        actor_id: str,
    ) -> TriggerDefinition:
        await self.authorize(actor_id, "triggers.manage", trigger_id)
        if definition.id != trigger_id:
            raise OpsMeshError("VALIDATION_ERROR", "Event trigger IDs cannot be changed")
        record = await self._record(trigger_id)
        self._version(record, expected_revision)
        definition = await self._prepare(definition, actor_id)
        definition = definition.model_copy(update={"revision": expected_revision + 1})
        await self.store.update(
            trigger_id,
            {
                **record.data,
                "definition": definition.model_dump(mode="json"),
                "creator_id": actor_id,
            },
            record.revision,
            events=(configuration_event(definition, actor_id, "updated"),),
        )
        return definition

    async def set_enabled(
        self,
        trigger_id: str,
        enabled: bool,
        expected_revision: int,
        *,
        actor_id: str,
    ) -> TriggerDefinition:
        await self.authorize(actor_id, "triggers.manage", trigger_id)
        record = await self._record(trigger_id)
        definition = self._version(record, expected_revision)
        if not enabled:
            disabled = definition.model_copy(
                update={
                    "enabled": False,
                    "revision": expected_revision + 1,
                }
            )
            await self.store.update(
                record.id,
                {**record.data, "definition": disabled.model_dump(mode="json")},
                record.revision,
                events=(configuration_event(disabled, actor_id, "updated"),),
            )
            return disabled
        return await self.update(
            trigger_id,
            definition.model_copy(update={"enabled": enabled}),
            expected_revision,
            actor_id=actor_id,
        )

    async def delete(self, trigger_id: str, expected_revision: int, *, actor_id: str) -> None:
        await self.authorize(actor_id, "triggers.manage", trigger_id)
        record = await self._record(trigger_id)
        definition = self._version(record, expected_revision)
        deleted = definition.model_copy(
            update={"enabled": False, "revision": expected_revision + 1}
        )
        await self.store.update(
            trigger_id,
            {
                **record.data,
                "deleted": True,
                "deleted_by": actor_id,
                "deleted_at": datetime.now(UTC).isoformat(),
                "definition": definition.model_copy(
                    update={
                        "enabled": False,
                        "revision": expected_revision + 1,
                    }
                ).model_dump(mode="json"),
            },
            record.revision,
            events=(configuration_event(deleted, actor_id, "deleted"),),
        )

    async def dispatch_event(self, event: OpsMeshEvent, *, actor_id: str) -> list[TriggerDelivery]:
        event_fingerprint(event)
        selected: list[tuple[TriggerDefinition, str]] = []
        for record in await self.store.list():
            if record.data.get("kind") != "trigger" or record.data.get("deleted"):
                continue
            definition = TriggerDefinition.model_validate(record.data["definition"])
            if not definition.enabled:
                continue
            if not matches(definition, event):
                accepted = await self.store.get(
                    f"_delivery:{delivery_identity(definition.id, event)}"
                )
                if accepted is None:
                    continue
            await self.authorize(actor_id, "triggers.execute", definition.id)
            selected.append((definition, str(record.data["creator_id"])))
        results: list[TriggerDelivery] = []
        for definition, creator_id in selected:
            result = await self.engine.deliver(definition, event, creator_id)
            results.append(result)
        return results

    async def ingest_webhook(
        self,
        trigger_id: str,
        body: bytes,
        *,
        timestamp: str,
        delivery_id: str,
        signature: str,
    ) -> TriggerDelivery:
        record = await self._record(trigger_id)
        definition = TriggerDefinition.model_validate(record.data["definition"])
        if not definition.enabled or not definition.webhook_secret_ref:
            raise OpsMeshError("NOT_FOUND", "Webhook trigger is not available")
        event = authenticate_webhook(
            definition,
            self.secret_resolver(definition.webhook_secret_ref),
            body,
            timestamp=timestamp,
            delivery_id=delivery_id,
            signature=signature,
        )
        return await self.engine.deliver(definition, event, str(record.data["creator_id"]))

    async def list_deliveries(
        self,
        trigger_id: str,
        *,
        actor_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TriggerDelivery]:
        await self.authorize(actor_id, "triggers.read", trigger_id)
        await self._record(trigger_id, include_deleted=True)
        if not 1 <= limit <= 1000 or offset < 0:
            raise OpsMeshError("VALIDATION_ERROR", "Invalid delivery pagination")
        rows = [
            r
            for r in await self.store.list()
            if r.data.get("kind") == "delivery" and r.data["delivery"]["trigger_id"] == trigger_id
        ]
        return [
            TriggerDelivery.model_validate(r.data["delivery"])
            for r in list(reversed(rows))[offset : offset + limit]
        ]

    async def reconcile(self) -> int:
        return await self.engine.reconcile()
