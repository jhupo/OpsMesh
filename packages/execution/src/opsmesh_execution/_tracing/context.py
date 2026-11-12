"""Optional identity dimensions for execution observations."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TraceContext:
    """Associate observations without changing the agent's runtime context.

    Attributes:
        user_id: Stable caller identifier, without credentials or personal data.
        session_id: Conversation identifier supplied by the calling plugin.
        tags: Searchable labels shared by observations in this execution.
    """

    user_id: str | None = None
    session_id: str | None = None
    tags: tuple[str, ...] = ()
