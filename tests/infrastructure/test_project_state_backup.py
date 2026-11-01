"""An offline restore retains pending group work and published project bindings."""

from datetime import UTC, datetime

import pytest

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.coordination.process_lock import LocalProcessLock
from opsmesh.maintenance.backup import create_backup, restore_backup
from opsmesh.modules.bots.conversations import (
    QueuedConversationMessage,
)


async def test_full_backup_restores_pending_queue_batch_capture_and_project_binding(tmp_path):
    settings = OpsMeshSettings(data_directory=tmp_path / "original", worker_enabled=False)
    app = OpsMeshApplication(settings)
    try:
        await app.initialize()
    except BaseException:
        await app.close()
        raise
    projects = settings.data_directory / "projects"
    projects.mkdir()
    (projects / "community.json").write_text('{"binding":"synthetic"}', encoding="utf-8")
    directory = settings.data_directory / "conversations" / "community"
    queue = app.services.conversation_inboxes.open("community")
    try:
        await queue.enqueue(
            QueuedConversationMessage(
                event_id="event",
                bot_id="bot",
                channel_id="group:one",
                user_id="member",
                message_id="message",
                text="pending question",
                occurred_at=datetime.now(UTC),
                to_me=False,
            )
        )
        await queue.set_moderation(
            "event", {"status": "ok", "action": "allow"}, eligible_for_chat=True
        )
        batch = await queue.claim_batch("bot", "group:one")
        assert batch is not None
        await queue.record_run(batch.batch_id, "pending-run")
        await queue.set_value("capture:pending", {"answer": "approved", "run_id": "pending-run"})
    finally:
        await app.close()
    lock = LocalProcessLock(directory / "consumer.lock")
    lock.acquire()
    try:
        with pytest.raises(OpsMeshError, match="Stop conversation consumers"):
            await create_backup(settings, tmp_path / "refused", maintenance=True)
        assert not (tmp_path / "refused").exists()
    finally:
        lock.release()
    bundle = tmp_path / "backup"
    manifest = await create_backup(settings, bundle, maintenance=True)
    assert {item.path for item in manifest.project_state_files} == {
        "projects/community.json",
    }
    restored = settings.model_copy(update={"data_directory": tmp_path / "restored"})
    await restore_backup(
        restored, bundle, expected_manifest_digest=manifest.manifest_digest, maintenance=True
    )
    assert (
        restored.data_directory / "projects" / "community.json"
    ).read_text() == '{"binding":"synthetic"}'
    app = OpsMeshApplication(restored)
    await app.services.database.verify_schema()
    queue = app.services.conversation_inboxes.open("community")
    try:
        pending = await queue.pending_batches()
        assert len(pending) == 1 and pending[0].run_id == "pending-run"
        assert await queue.get_value("capture:pending") == {
            "answer": "approved",
            "run_id": "pending-run",
        }
    finally:
        await app.close()
