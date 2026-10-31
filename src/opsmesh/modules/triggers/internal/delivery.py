"""Persistent delivery deduplication, submission leases and Run state projection."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from opsmesh.framework.errors import ErrorInfo, OpsMeshError
from opsmesh.framework.events import OpsMeshEvent
from opsmesh.infrastructure.database import StoredRecord, VersionedRecordStore
from opsmesh.modules.runtime.api import TERMINAL_STATES
from opsmesh.modules.triggers.api import (
    TriggerDefinition,
    TriggerDelivery,
    TriggerRunResolver,
    TriggerSubmissionResolver,
    TriggerSubmitter,
    TriggerTargetValidator,
)
from opsmesh.modules.triggers.events import delivery_event

from .matching import build_invocation, delivery_identity, event_fingerprint, matches


class TriggerDeliveryEngine:
    def __init__(
        self,
        store: VersionedRecordStore,
        submitter: TriggerSubmitter,
        validate: TriggerTargetValidator,
        run_resolver: TriggerRunResolver | None,
        *,
        lease_seconds: float = 60,
        submission_resolver: TriggerSubmissionResolver | None = None,
    ) -> None:
        self.store, self.submitter, self.validate = store, submitter, validate
        self.run_resolver, self.lease_seconds = run_resolver, lease_seconds
        self.submission_resolver = submission_resolver

    async def deliver(
        self,
        definition: TriggerDefinition,
        event: OpsMeshEvent,
        creator_id: str,
    ) -> TriggerDelivery:
        fingerprint = event_fingerprint(event)
        identity = delivery_identity(definition.id, event)
        key = f"_delivery:{identity}"
        record = await self.store.get(key)
        owner = uuid4().hex
        now = datetime.now(UTC)
        if record is None:
            delivery = TriggerDelivery(
                id=identity,
                trigger_id=definition.id,
                trigger_revision=definition.revision,
                event_id=event.id,
                status="dispatching",
                created_at=now,
                updated_at=now,
            )
            try:
                record = await self.store.create(
                    key,
                    {
                        "kind": "delivery",
                        "delivery": delivery.model_dump(mode="json"),
                        "configuration": definition.model_dump(mode="json"),
                        "event": event.model_dump(
                            mode="json", include={"id", "type", "source", "occurred_at", "payload"}
                        ),
                        "creator_id": creator_id,
                        "fingerprint": fingerprint,
                        "lease_owner": owner,
                        "lease_expires_at": (
                            now + timedelta(seconds=self.lease_seconds)
                        ).isoformat(),
                    },
                )
            except OpsMeshError as exc:
                if exc.code != "CONFLICT":
                    raise
                record = await self.store.get(key)
                assert record is not None
        if record.data["fingerprint"] != fingerprint:
            raise OpsMeshError(
                "IDEMPOTENCY_CONFLICT", "Delivery ID was reused for a different event"
            )
        delivery = TriggerDelivery.model_validate(record.data["delivery"])
        if delivery.status != "dispatching":
            return delivery
        if record.data["lease_owner"] != owner:
            if datetime.fromisoformat(record.data["lease_expires_at"]) > now:
                return delivery
            try:
                record = await self.store.update(
                    key,
                    {
                        **record.data,
                        "lease_owner": owner,
                        "lease_expires_at": (
                            now + timedelta(seconds=self.lease_seconds)
                        ).isoformat(),
                    },
                    record.revision,
                )
            except OpsMeshError as exc:
                if exc.code != "CONFLICT":
                    raise
                return delivery
        # Recovery uses the configuration captured before the first submission.
        definition = TriggerDefinition.model_validate(record.data["configuration"])
        accepted_event = record.data.get("event")
        if accepted_event is None:
            raise OpsMeshError(
                "CHECKPOINT_INCOMPATIBLE", "Trigger delivery has no accepted event snapshot"
            )
        return await self._submit(record, definition, OpsMeshEvent.model_validate(accepted_event))

    async def _submit(
        self, record: StoredRecord, definition: TriggerDefinition, event: OpsMeshEvent
    ) -> TriggerDelivery:
        delivery = TriggerDelivery.model_validate(record.data["delivery"])
        now = datetime.now(UTC)
        if not matches(definition, event):
            ignored = delivery.model_copy(
                update={
                    "status": "ignored",
                    "reason": "filter_mismatch",
                    "updated_at": now,
                    "finished_at": now,
                }
            )
            await self._save(record, ignored)
            return ignored
        try:
            async with asyncio.timeout(self.lease_seconds * 0.8):
                await self.validate(definition, str(record.data["creator_id"]))
                invocation = build_invocation(definition, event, delivery.id)
                run_id = await self.submitter(invocation)
                if not run_id:
                    raise OpsMeshError(
                        "TRIGGER_RESULT_MISSING", "Trigger submitter did not return a Run ID"
                    )
            updated = delivery.model_copy(
                update={
                    "status": "submitted",
                    "run_id": run_id,
                    "updated_at": datetime.now(UTC),
                }
            )
        except Exception as exc:
            uncertain = not isinstance(exc, OpsMeshError) or (
                exc.code
                not in {"IDEMPOTENCY_CONFLICT", "CONFLICT", "FORBIDDEN", "VALIDATION_ERROR"}
                and (exc.retryable or exc.code == "TRIGGER_RESULT_MISSING")
            )
            if uncertain and self.submission_resolver is not None:
                recovered = await self.submission_resolver(
                    definition.principal_id, f"trigger:{delivery.id}"
                )
                if recovered is not None:
                    if (
                        recovered.principal_id == definition.principal_id
                        and recovered.published_agent_ref == definition.agent_revision
                        and recovered.metadata.get("trigger_id") == definition.id
                        and recovered.metadata.get("trigger_delivery_id") == delivery.id
                        and recovered.metadata.get("event_id") == event.id
                    ):
                        submitted = delivery.model_copy(
                            update={
                                "status": "submitted",
                                "run_id": recovered.id,
                                "updated_at": datetime.now(UTC),
                            }
                        )
                        await self._save(record, submitted)
                        return submitted
                    exc = OpsMeshError(
                        "IDEMPOTENCY_CONFLICT", "Existing Run does not match the trigger delivery"
                    )
            error = ErrorInfo(
                code=exc.code if isinstance(exc, OpsMeshError) else "TRIGGER_FAILED",
                safe_message=exc.message
                if isinstance(exc, OpsMeshError)
                else "Event trigger submission failed",
                phase="trigger",
            )
            updated = delivery.model_copy(
                update={
                    "status": "failed",
                    "error": error,
                    "updated_at": datetime.now(UTC),
                    "finished_at": datetime.now(UTC),
                }
            )
        await self._save(record, updated)
        return updated

    async def _save(self, record: StoredRecord, delivery: TriggerDelivery) -> None:
        # CAS prevents an expired owner from overwriting a successor's acknowledgment.
        if datetime.fromisoformat(record.data["lease_expires_at"]) <= datetime.now(UTC):
            raise OpsMeshError(
                "EVENT_IN_PROGRESS", "Trigger submission lease expired", retryable=True
            )
        await self.store.update(
            record.id,
            {
                **record.data,
                "delivery": delivery.model_dump(mode="json"),
            },
            record.revision,
            events=(delivery_event(delivery, str(record.data["configuration"]["principal_id"])),),
        )

    async def reconcile(self) -> int:
        if self.run_resolver is None:
            return 0
        changed = 0
        for record in await self.store.list():
            if record.data.get("kind") != "delivery":
                continue
            delivery = TriggerDelivery.model_validate(record.data["delivery"])
            if not delivery.run_id or delivery.status not in {
                "submitted",
                "running",
                "waiting_approval",
            }:
                continue
            run = await self.run_resolver(delivery.run_id)
            status = (
                "submitted"
                if run.status == "queued"
                else "running"
                if run.status in {"preparing", "finalizing", "resuming"}
                else run.status
            )
            if status == delivery.status:
                continue
            updated = delivery.model_copy(
                update={
                    "status": status,
                    "updated_at": datetime.now(UTC),
                    "finished_at": run.updated_at if status in TERMINAL_STATES else None,
                    "error": ErrorInfo(
                        code=run.error.code, safe_message=run.error.safe_message, phase="run"
                    )
                    if run.error
                    else None,
                }
            )
            try:
                await self.store.update(
                    record.id,
                    {
                        **record.data,
                        "delivery": updated.model_dump(mode="json"),
                    },
                    record.revision,
                    events=(
                        delivery_event(updated, str(record.data["configuration"]["principal_id"])),
                    ),
                )
                changed += 1
            except OpsMeshError as exc:
                if exc.code != "CONFLICT":
                    raise
        return changed
