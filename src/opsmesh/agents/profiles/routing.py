"""User-owned native SDK graph policy and admission budgets."""

from dataclasses import dataclass
from uuid import UUID

from opsmesh.shared.utils import dict_or_empty, uuid_or_none

MAX_AGENT_TOOL_DEPTH = 3
MAX_AGENT_TOOL_TURNS = 20
MAX_AGENT_TOOL_TARGETS = 10
MAX_AGENT_TOOL_GRAPH_NODES = 25


@dataclass(frozen=True, slots=True)
class AgentTargetPolicy:
    allowed_profile_ids: tuple[UUID, ...] | None
    max_depth: int
    max_turns: int
    max_targets: int


def bounded_target_int(value: object, name: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"Agent tool {name} must be between {minimum} and {maximum}")
    return value


def agent_target_policy(tool_policy: object, kind: str = "agent_tools") -> AgentTargetPolicy | None:
    raw = tool_policy.get(kind) if isinstance(tool_policy, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("Agent tool policy must be an object")
    if raw.get("enabled") is not True:
        return None
    raw_allowed = raw.get("allowed_agent_profile_ids")
    allowed_profile_ids: tuple[UUID, ...] | None
    if raw_allowed == "*":
        allowed_profile_ids = None
    elif isinstance(raw_allowed, list):
        parsed = tuple(uuid_or_none(item) for item in raw_allowed)
        if any(item is None for item in parsed) or len(set(parsed)) != len(parsed):
            raise ValueError("Agent tool allowed profile IDs are invalid or duplicated")
        allowed_profile_ids = tuple(item for item in parsed if item is not None)
    else:
        raise ValueError("Agent tool policy requires allowed_agent_profile_ids")
    return AgentTargetPolicy(
        allowed_profile_ids=allowed_profile_ids,
        max_depth=bounded_target_int(raw.get("max_depth", 1), "max_depth", 1, MAX_AGENT_TOOL_DEPTH),
        max_turns=bounded_target_int(raw.get("max_turns", 8), "max_turns", 1, MAX_AGENT_TOOL_TURNS),
        max_targets=bounded_target_int(
            raw.get("max_targets", 5),
            "max_targets",
            1,
            MAX_AGENT_TOOL_TARGETS,
        ),
    )


def require_handoff_policy(tool_policy: object) -> bool:
    raw = dict_or_empty(dict_or_empty(tool_policy).get("handoffs"))
    required = raw.get("require_handoff", False)
    if not isinstance(required, bool):
        raise ValueError("require_handoff must be a boolean")
    if required and (
        agent_target_policy(tool_policy, "handoffs") is None
        or not raw.get("allowed_agent_profile_ids")
    ):
        raise ValueError("A routing-only agent requires enabled handoff targets")
    return required
