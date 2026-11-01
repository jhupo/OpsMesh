from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from opsmesh.framework.adapters import BotRef, ReplyTarget
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.message import DispatchResult
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.runtime.api import AgentRunView, RunOverrides
from opsmesh.modules.scheduling.api import PersistentSchedule
from opsmesh.modules.scheduling.internal.service import PersistentScheduler


@pytest_asyncio.fixture
async def harness(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'schedules.db').as_posix()}")
    store = VersionedRecordStore(database, "schedules")
    await database.initialize()
    contexts = []
    statuses = {}
    denied = set()
    checked = []
    notifications = []

    async def authorize(actor, action, resource):
        checked.append((actor, action, resource))
        if (actor, action) in denied:
            raise OpsMeshError("FORBIDDEN", "Not allowed")

    async def resolve(reference):
        if reference not in {"helper", "helper@1"}:
            raise OpsMeshError("NOT_FOUND", "Published Agent does not exist")
        return AgentSpecification(id="helper", name="Helper")

    async def dispatch(context):
        contexts.append(context)
        if context.values.get("agent_revision"):
            context.values["run_id"] = str(len(contexts))
            statuses[str(len(contexts))] = "queued"
        return DispatchResult(context.event.id, matched=1)

    async def run(run_id):
        return AgentRunView(
            id=run_id,
            revision=1,
            principal_id="owner",
            session_id="session",
            agent=await resolve("helper"),
            status=statuses[run_id],
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def validate(schedule, actor):
        checked.append((actor, "target", schedule.id))

    async def notify(occurrence, schedule):
        notifications.append(occurrence)

    scheduler = PersistentScheduler(
        OpsMeshDriver(),
        store,
        authorize,
        dispatch,
        agent_resolver=resolve,
        run_resolver=run,
        validate_target=validate,
        occurrence_observer=notify,
    )
    yield scheduler, contexts, statuses, denied, checked, notifications
    await database.close()


async def test_identity_delegation_and_agent_permissions_are_service_owned(harness):
    scheduler, _, _, denied, checked, _ = harness
    schedule = PersistentSchedule(id="privileged", principal_id="admin", agent_revision="helper")
    denied.add(("manager", "schedules:impersonate"))
    with pytest.raises(OpsMeshError, match="Not allowed"):
        await scheduler.create(schedule, actor_id="manager")
    assert await scheduler.list_schedules(actor_id="manager") == []
    denied.clear()
    denied.add(("manager", "agent:run"))
    with pytest.raises(OpsMeshError, match="Not allowed"):
        await scheduler.create(schedule, actor_id="manager")
    denied.clear()
    denied.add(("admin", "agent:run"))
    with pytest.raises(OpsMeshError, match="Not allowed"):
        await scheduler.create(schedule, actor_id="manager")
    denied.clear()
    published = await scheduler.create(schedule, actor_id="manager")
    assert published.agent_revision == "helper@1"
    assert ("manager", "schedules:impersonate", "admin") in checked
    assert ("admin", "schedules.execute", "privileged") in checked


async def test_unknown_version_rejected_and_revoked_principal_fails_at_dispatch(harness):
    scheduler, contexts, _, denied, _, _ = harness
    with pytest.raises(OpsMeshError, match="Published Agent"):
        await scheduler.create(
            PersistentSchedule(id="bad", principal_id="owner", agent_revision="missing"),
            actor_id="owner",
        )
    await scheduler.create(
        PersistentSchedule(id="revoked", principal_id="owner", agent_revision="helper"),
        actor_id="owner",
    )
    denied.add(("owner", "schedules.execute"))
    assert await scheduler.tick() == 0
    assert contexts == []
    (occurrence,) = await scheduler.list_occurrences("revoked", actor_id="owner")
    assert occurrence.status == "failed" and occurrence.error.code == "FORBIDDEN"


async def test_crud_revision_preview_manual_idempotency_and_tombstone(harness):
    scheduler, contexts, _, _, _, _ = harness
    future = datetime.now(UTC) + timedelta(days=1)
    created = await scheduler.create(
        PersistentSchedule(
            id="managed", principal_id="owner", next_run_at=future, interval_seconds=60
        ),
        actor_id="owner",
    )
    updated = await scheduler.update(
        "managed", created.model_copy(update={"input": "new"}), 1, actor_id="owner"
    )
    assert updated.revision == 2
    with pytest.raises(OpsMeshError, match="configuration was changed"):
        await scheduler.delete("managed", 1, actor_id="owner")
    preview = await scheduler.preview("managed", 2, actor_id="owner", count=2)
    assert preview.times == [future, future + timedelta(seconds=60)]
    fired = await scheduler.run_now("managed", 2, actor_id="owner", idempotency_key="request-1")
    retry = await scheduler.run_now("managed", 2, actor_id="owner", idempotency_key="request-1")
    assert fired == retry and fired.status == "completed" and len(contexts) == 1
    assert (await scheduler.get("managed", actor_id="owner")).next_run_at == future
    await scheduler.delete("managed", 2, actor_id="owner")
    assert await scheduler.list_schedules(actor_id="owner") == []
    assert len(await scheduler.list_occurrences("managed", actor_id="owner")) == 1
    with pytest.raises(OpsMeshError, match="does not exist"):
        await scheduler.get("managed", actor_id="owner")
    with pytest.raises(OpsMeshError):
        await scheduler.create(created, actor_id="owner")


async def test_input_resources_delivery_and_run_completion_are_separate(harness):
    scheduler, contexts, statuses, _, checked, notifications = harness
    target = ReplyTarget(bot=BotRef(adapter_id="qq", bot_id="bot"), channel_id="group:10")
    schedule = await scheduler.create(
        PersistentSchedule(
            id="rich",
            principal_id="owner",
            agent_revision="helper",
            input=[{"role": "user", "content": "report"}],
            run_overrides=RunOverrides(workspace_id="report-files"),
            reply_target=target,
            session_policy="shared",
            session_id="reports",
        ),
        actor_id="owner",
    )
    assert await scheduler.tick() == 1
    (occurrence,) = await scheduler.list_occurrences("rich", actor_id="owner")
    assert occurrence.status == "submitted" and occurrence.run_id == "1"
    assert occurrence.finished_at is None
    (context,) = contexts
    assert context.reply_target == target
    assert context.values["input"] == schedule.input
    assert context.values["run_overrides"]["workspace_id"] == "report-files"
    assert context.values["session_id"] == "reports"
    assert ("owner", "target", "rich") in checked
    statuses["1"] = "waiting_approval"
    assert await scheduler.reconcile() == 1
    assert notifications[-1].status == "waiting_approval"
    assert await scheduler.reconcile() == 0
    statuses["1"] = "completed"
    assert await scheduler.reconcile() == 1
    (completed,) = await scheduler.list_occurrences("rich", actor_id="owner")
    assert completed.status == "completed" and completed.finished_at is not None


async def test_concurrency_queue_is_durable_and_drains_after_run_completion(harness):
    scheduler, contexts, statuses, _, _, _ = harness
    due = datetime.now(UTC)
    await scheduler.create(
        PersistentSchedule(
            id="queue",
            principal_id="owner",
            agent_revision="helper",
            next_run_at=due,
            interval_seconds=10,
            misfire_policy="catch_up",
            max_catch_up=3,
            max_active_runs=1,
        ),
        actor_id="owner",
    )
    assert await scheduler.tick(now=due + timedelta(seconds=20)) == 1
    rows = await scheduler.list_occurrences("queue", actor_id="owner")
    assert [r.status for r in rows].count("queued") == 2
    assert [r.status for r in rows].count("submitted") == 1
    assert await scheduler.tick(now=due + timedelta(seconds=20)) == 0
    statuses["1"] = "completed"
    assert await scheduler.tick(now=due + timedelta(seconds=20)) == 1
    assert len(contexts) == 2
    assert contexts[0].event.id != contexts[1].event.id


async def test_concurrency_skip_and_queue_bound_have_persisted_reasons(harness):
    scheduler, _, _, _, _, _ = harness
    due = datetime.now(UTC)
    await scheduler.create(
        PersistentSchedule(
            id="skip",
            principal_id="owner",
            agent_revision="helper",
            next_run_at=due,
            interval_seconds=10,
            concurrency_policy="skip",
        ),
        actor_id="owner",
    )
    assert await scheduler.tick(now=due) == 1
    assert await scheduler.tick(now=due + timedelta(seconds=10)) == 0
    rows = await scheduler.list_occurrences("skip", actor_id="owner")
    assert rows[0].status == "skipped" and rows[0].reason == "concurrency_limit"
    await scheduler.create(
        PersistentSchedule(
            id="bounded",
            principal_id="owner",
            agent_revision="helper",
            next_run_at=due,
            interval_seconds=10,
            misfire_policy="catch_up",
            max_queued_occurrences=1,
        ),
        actor_id="owner",
    )
    await scheduler.tick(now=due + timedelta(seconds=30))
    rows = await scheduler.list_occurrences("bounded", actor_id="owner")
    assert len([r for r in rows if r.reason == "queue_full"]) == 3


@pytest.mark.parametrize(
    "policy,count,status",
    [("skip", 0, "skipped"), ("coalesce", 1, "completed"), ("catch_up", 3, "completed")],
)
async def test_misfire_policies(harness, policy, count, status):
    scheduler, contexts, _, _, _, _ = harness
    due = datetime.now(UTC) - timedelta(minutes=10)
    await scheduler.create(
        PersistentSchedule(
            id="missed",
            principal_id="owner",
            next_run_at=due,
            interval_seconds=30,
            misfire_policy=policy,
            max_catch_up=3,
            misfire_grace_seconds=5,
        ),
        actor_id="owner",
    )
    assert await scheduler.tick(now=due + timedelta(minutes=10)) == count
    assert len(contexts) == count
    occurrences = await scheduler.list_occurrences("missed", actor_id="owner")
    assert all(r.status == status for r in occurrences)


async def test_failed_dispatch_has_sanitized_persistent_error_and_notification(harness):
    scheduler, _, _, _, _, notifications = harness

    async def fail(context):
        raise RuntimeError("secret=must-not-be-in-record")

    scheduler.engine.dispatcher = fail
    await scheduler.create(PersistentSchedule(id="failure", principal_id="owner"), actor_id="owner")
    assert await scheduler.tick() == 0
    (occurrence,) = await scheduler.list_occurrences("failure", actor_id="owner")
    assert occurrence.status == "failed" and occurrence.error.code == "SCHEDULE_FAILED"
    assert "must-not" not in occurrence.model_dump_json()
    assert notifications == [occurrence]
    assert not (await scheduler.get("failure", actor_id="owner")).enabled


async def test_manual_trigger_applies_skip_concurrency_policy(harness):
    scheduler, contexts, _, _, _, _ = harness
    schedule = await scheduler.create(
        PersistentSchedule(
            id="manual",
            principal_id="owner",
            agent_revision="helper",
            concurrency_policy="skip",
            next_run_at=datetime.now(UTC) + timedelta(days=1),
        ),
        actor_id="owner",
    )
    first = await scheduler.run_now(
        "manual", schedule.revision, actor_id="owner", idempotency_key="first"
    )
    second = await scheduler.run_now(
        "manual", schedule.revision, actor_id="owner", idempotency_key="second"
    )
    assert first.status == "submitted"
    assert second.status == "skipped" and second.reason == "concurrency_limit"
    assert len(contexts) == 1


async def test_failed_notification_is_retried_from_durable_occurrence(harness):
    scheduler, _, _, _, _, _ = harness
    notifications = []

    async def fail_dispatch(context):
        raise RuntimeError("dispatch failed")

    async def transient_observer(occurrence, schedule):
        notifications.append(occurrence.id)
        if len(notifications) == 1:
            raise RuntimeError("database unavailable")

    scheduler.engine.dispatcher = fail_dispatch
    scheduler.engine.observer = transient_observer
    await scheduler.create(PersistentSchedule(id="notify", principal_id="owner"), actor_id="owner")
    assert await scheduler.tick() == 0
    assert len(notifications) == 1
    await scheduler.reconcile()
    await scheduler.reconcile()
    assert len(notifications) == 2 and notifications[0] == notifications[1]


async def test_legacy_plan_rows_upgrade_without_a_new_table(harness):
    scheduler, contexts, _, _, _, _ = harness
    due = datetime.now(UTC)
    await scheduler.store.create(
        "legacy",
        {
            "schedule": {
                "id": "legacy",
                "principal_id": "owner",
                "next_run_at": due.isoformat(),
                "interval_seconds": None,
                "agent_revision": None,
                "payload": {},
                "enabled": True,
            },
            "state": "ready",
        },
    )
    assert (await scheduler.get("legacy", actor_id="owner")).revision == 1
    assert await scheduler.tick(now=due) == 1
    assert len(contexts) == 1
    record = await scheduler.store.get("legacy")
    assert record.data["kind"] == "schedule"
    assert len(await scheduler.list_occurrences("legacy", actor_id="owner")) == 1


async def test_schedule_changes_and_occurrences_have_atomic_actor_audit_facts(harness):
    scheduler, _, _, _, _, _ = harness
    created = await scheduler.create(
        PersistentSchedule(id="audited", principal_id="owner"), actor_id="owner"
    )
    await scheduler.tick()
    await scheduler.delete("audited", created.revision, actor_id="owner")
    facts = await scheduler.store.database.pending_events()
    selected = [f for f in facts if f["source"] == "scheduling"]
    assert {f["type"] for f in selected} >= {
        "scheduling.schedule.created",
        "scheduling.schedule.deleted",
        "scheduling.occurrence.completed",
    }
    assert all(f["actor_ref"] == "owner" for f in selected)


async def test_unhandled_timer_is_not_reported_as_success(harness):
    scheduler, _, _, _, _, _ = harness

    async def unhandled(context):
        return DispatchResult(context.event.id, matched=0)

    scheduler.engine.dispatcher = unhandled
    await scheduler.create(
        PersistentSchedule(id="日报任务", principal_id="owner"), actor_id="owner"
    )
    assert await scheduler.tick() == 0
    (occurrence,) = await scheduler.list_occurrences("日报任务", actor_id="owner")
    assert occurrence.status == "failed" and occurrence.error.code == "SCHEDULE_UNHANDLED"


async def test_manager_can_disable_after_execution_permission_is_revoked(harness):
    scheduler, _, _, denied, _, _ = harness
    created = await scheduler.create(
        PersistentSchedule(
            id="withdrawn",
            principal_id="owner",
            agent_revision="helper",
        ),
        actor_id="owner",
    )
    denied.add(("owner", "agent:run"))
    disabled = await scheduler.set_enabled(
        "withdrawn", False, actor_id="owner", expected_revision=created.revision
    )
    assert disabled.enabled is False
    with pytest.raises(OpsMeshError, match="Not allowed"):
        await scheduler.set_enabled(
            "withdrawn", True, actor_id="owner", expected_revision=disabled.revision
        )


@pytest.mark.parametrize("mode", ["ack_lost", "wrong_metadata", "explicit_conflict"])
async def test_submission_lookup_recovers_only_matching_committed_run(harness, mode):
    scheduler, _, _, _, _, _ = harness
    committed = []

    async def lost_ack(context):
        metadata = {key: context.values[key] for key in ("schedule_id", "occurrence_id")}
        metadata["event_id"] = context.event.id if mode != "wrong_metadata" else "unrelated"
        committed.append(
            AgentRunView(
                id="committed",
                revision=1,
                principal_id="owner",
                session_id="session",
                agent=AgentSpecification(id="helper", name="Helper"),
                published_agent_ref="helper@1",
                status="queued",
                metadata=metadata,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        if mode == "explicit_conflict":
            raise OpsMeshError("IDEMPOTENCY_CONFLICT", "Different submission", retryable=True)
        raise RuntimeError("Acknowledgment lost after commit")

    async def lookup(principal_id, key):
        return committed[0]

    scheduler.engine.dispatcher = lost_ack
    scheduler.engine.submission_resolver = lookup
    await scheduler.create(
        PersistentSchedule(
            id="recover",
            principal_id="owner",
            agent_revision="helper",
            next_run_at=datetime.now(UTC) + timedelta(days=1),
        ),
        actor_id="owner",
    )
    result = await scheduler.run_now("recover", 1, actor_id="owner", idempotency_key="accepted")
    if mode == "ack_lost":
        assert result.status == "submitted" and result.run_id == "committed"
        assert (await scheduler.get("recover", actor_id="owner")).enabled
    else:
        assert result.status == "failed" and result.run_id is None
        assert result.error.code == "IDEMPOTENCY_CONFLICT"
