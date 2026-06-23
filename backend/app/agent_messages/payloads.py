from backend.app.api.schemas.agent_messages import (
    AgentMessageCreateRequest,
    AgentMessageThreadCreateRequest,
)


def thread_create_payload(data: AgentMessageThreadCreateRequest) -> dict[str, object]:
    return data.model_dump(exclude={"agent_team_id"})


def message_create_payload(data: AgentMessageCreateRequest) -> dict[str, object]:
    return data.model_dump(exclude={"task_id", "agent_team_id"})
