from backend.app.agent_runtime.errors import AgentRuntimePolicyError


class ProjectRunIOError(AgentRuntimePolicyError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        stage: str,
        retryable: bool,
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            event_type=f"project.{stage}.failed",
            metadata={"stage": stage, **(metadata or {})},
            retryable=retryable,
        )
