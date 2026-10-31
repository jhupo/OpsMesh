"""Durable delegated outputs, trace-independent idempotency and transition metrics."""

from collections import Counter

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.internal.service import PersistentAgentRuntime
from tests.runtime.test_runtime import ControlledBackend, create_runtime, request


def receipt(call_id, snapshot):
    return {
        "workspace_id": f"child-{call_id}",
        "workspace_snapshot_id": snapshot,
        "artifacts": [{"path": "result.txt", "digest": snapshot}],
        "delegated_agent": "child@1",
        "tool_call_id": call_id,
    }


async def test_delegated_artifacts_survive_approval_and_merge_by_tool_call(tmp_path):
    class Backend(ControlledBackend):
        async def run(self, spec, input, context, **options):
            restored = context.services["delegated_artifacts"]
            if options.get("checkpoint"):
                assert restored == [receipt("child-a", "paused")]
                restored.extend([receipt("child-a", "completed"), receipt("child-b", "second")])
            else:
                restored.append(receipt("child-a", "paused"))
            return await super().run(spec, input, context, **options)

    finalizer_receipts = []

    async def finalize(view, outcome):
        finalizer_receipts.extend(view.metadata["delegated_artifacts"])
        return {"root_result": "saved"}

    runtime, database = await create_runtime(tmp_path, Backend(pause=True), finalizer=finalize)
    try:
        run = await runtime.submit(request())
        waiting = await runtime.execute(run.id)
        assert waiting.agent.sandbox is None
        assert waiting.metadata["delegated_artifacts"] == [receipt("child-a", "paused")]
        worker = PersistentAgentRuntime(
            database, runtime.extensions, runtime.context_factory, finalizer=finalize
        )
        decided = await worker.decide(run.id, "approve1", True, waiting.revision)
        await worker.resume(run.id, decided.revision)
        completed = await worker.execute(run.id)
        expected = [receipt("child-a", "completed"), receipt("child-b", "second")]
        assert completed.metadata["delegated_artifacts"] == expected
        assert finalizer_receipts == expected
        assert completed.metadata["root_result"] == "saved"
    finally:
        await database.close()


async def test_finalizer_restart_preserves_child_receipts_without_reexecuting_model(tmp_path):
    class WorkerCrash(BaseException):
        pass

    class Backend(ControlledBackend):
        async def run(self, spec, input, context, **options):
            context.services["delegated_artifacts"].append(receipt("child", "saved"))
            return await super().run(spec, input, context, **options)

    async def interrupted_finalizer(view, outcome):
        assert view.metadata["delegated_artifacts"] == [receipt("child", "saved")]
        raise WorkerCrash()

    async def recovered_finalizer(view, outcome):
        assert view.metadata["delegated_artifacts"] == [receipt("child", "saved")]
        return {"finalized": True}

    backend = Backend()
    runtime, database = await create_runtime(tmp_path, backend, finalizer=interrupted_finalizer)
    try:
        run = await runtime.submit(request())
        with pytest.raises(WorkerCrash):
            await runtime.execute(run.id)
        saved = await runtime.runs.get(run.id)
        assert saved.data["status"] == "finalizing" and saved.data["final_outcome"]
        expired = await runtime.runs.update(
            run.id, dict(saved.data, lease_expires=0), saved.revision
        )
        worker = PersistentAgentRuntime(
            database, runtime.extensions, runtime.context_factory, finalizer=recovered_finalizer
        )
        await worker.recover(run.id, expired.revision)
        result = await worker.execute(run.id)
        assert result.status == "completed" and backend.calls == 1
        assert result.metadata["delegated_artifacts"] == [receipt("child", "saved")]
    finally:
        await database.close()


async def test_request_trace_context_does_not_change_idempotency_identity(tmp_path):
    runtime, database = await create_runtime(tmp_path, ControlledBackend())
    try:
        first = await runtime.submit(
            request().model_copy(
                update={"metadata": {"trace_context": {"traceparent": "first"}, "business": "same"}}
            )
        )
        second = await runtime.submit(
            request().model_copy(
                update={
                    "metadata": {"trace_context": {"traceparent": "second"}, "business": "same"}
                }
            )
        )
        assert first.id == second.id
        assert second.metadata["trace_context"] == {"traceparent": "first"}
        with pytest.raises(OpsMeshError, match="different input"):
            await runtime.submit(
                request().model_copy(
                    update={
                        "metadata": {
                            "trace_context": {"traceparent": "second"},
                            "business": "different",
                        }
                    }
                )
            )
    finally:
        await database.close()


async def test_metrics_use_persistent_queue_and_approval_timestamps_once(tmp_path):
    observations = []

    def observer(name, value):
        observations.append((name, value))

    runtime, database = await create_runtime(
        tmp_path, ControlledBackend(pause=True), observe_metric=observer
    )
    try:
        run = await runtime.submit(request())
        queued = await runtime.runs.get(run.id)
        await runtime.runs.update(
            run.id, dict(queued.data, queued_at=queued.created_at.timestamp() - 7), queued.revision
        )
        waiting = await runtime.execute(run.id)
        assert observations[0][0] == "queue_wait_seconds" and observations[0][1] >= 7
        record = await runtime.runs.get(run.id)
        await runtime.runs.update(
            run.id,
            dict(record.data, approval_wait_started_at=record.updated_at.timestamp() - 5),
            record.revision,
        )
        worker = PersistentAgentRuntime(
            database, runtime.extensions, runtime.context_factory, observe_metric=observer
        )
        waiting = await worker.get_run(run.id)
        decided = await worker.decide(run.id, "approve1", True, waiting.revision)
        await worker.resume(run.id, decided.revision)
        assert dict(observations)["approval_wait_seconds"] >= 5
        assert (await worker.execute(run.id)).status == "completed"
        await worker.execute(run.id)
        await worker.cancel(run.id)
        assert Counter(name for name, _ in observations) == {
            "queue_wait_seconds": 2,
            "approval_wait_seconds": 1,
            "run_duration_seconds": 1,
        }
        assert all(value >= 0 for _, value in observations)
    finally:
        await database.close()


async def test_cancellation_and_context_failure_record_terminal_duration(tmp_path):
    observations = []
    runtime, database = await create_runtime(
        tmp_path, ControlledBackend(), observe_metric=lambda *args: observations.append(args)
    )
    try:
        queued = await runtime.submit(request("cancel"))
        await runtime.cancel(queued.id)

        async def broken_context(view):
            raise RuntimeError("Context service unavailable")

        runtime.context_factory = broken_context
        queued = await runtime.submit(request("failure"))
        assert (await runtime.execute(queued.id)).status == "failed"
        assert Counter(name for name, _ in observations) == {
            "queue_wait_seconds": 1,
            "run_duration_seconds": 2,
        }
    finally:
        await database.close()


async def test_metrics_failure_cannot_fail_successful_run(tmp_path):
    def broken_observer(name, value):
        raise RuntimeError("Metrics unavailable")

    runtime, database = await create_runtime(
        tmp_path, ControlledBackend(), observe_metric=broken_observer
    )
    try:
        run = await runtime.submit(request())
        assert (await runtime.execute(run.id)).status == "completed"
    finally:
        await database.close()
