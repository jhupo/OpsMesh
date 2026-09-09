from __future__ import annotations

import json
from uuid import uuid4

from backend.app.workers.jobs import JobPayload
from backend.app.workers.queue.contracts import ProcessingEntry, QueueStorage


class QueueSerializationMixin:
    def _serialize(self: QueueStorage, job: JobPayload) -> str:
        return job.model_dump_json()

    def _deserialize(self: QueueStorage, raw_payload: bytes | str) -> JobPayload:
        if isinstance(raw_payload, bytes):
            raw_payload = raw_payload.decode("utf-8")
        return JobPayload.model_validate(json.loads(raw_payload))

    def _processing_key(self: QueueStorage) -> str:
        return f"{self.keys.queue(self.queue_name)}:processing"

    def _retry_key(self: QueueStorage) -> str:
        return f"{self.keys.queue(self.queue_name)}:retry"

    def _serialize_processing_entry(self: QueueStorage, raw_payload: bytes | str) -> str:
        payload = raw_payload.decode("utf-8") if isinstance(raw_payload, bytes) else raw_payload
        return json.dumps(
            {"lease_id": str(uuid4()), "payload": payload},
            separators=(",", ":"),
        )

    def _deserialize_processing_entry(
        self: QueueStorage,
        raw_entry: bytes | str,
    ) -> ProcessingEntry:
        if isinstance(raw_entry, bytes):
            raw_entry = raw_entry.decode("utf-8")
        entry = json.loads(raw_entry)
        payload = entry["payload"]
        return {"payload": payload, "job": self._deserialize(payload)}
