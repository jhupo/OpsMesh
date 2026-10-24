from opsmesh.shared.schemas import RecordView


class SessionView(RecordView):
    owner_id: str
    caller_id: str | None
    agent_key: str
    scope: str
    channel_id: str | None
    conversation_id: str | None
    active_task_id: str | None
    revision: int
    closed: bool
    sandbox_backend: str | None
    sandbox_dirty: bool
