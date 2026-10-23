"""Native handoff options with registered callbacks and target Agent references."""

from pydantic import model_validator

from .base import ConfigValue, Identifier
from .callbacks import CallbackRef


class HandoffRef(ConfigValue):
    agent_id: Identifier
    tool_name_override: str | None = None
    tool_description_override: str | None = None
    on_handoff: CallbackRef | None = None
    input_type: CallbackRef | None = None
    input_filter: CallbackRef | None = None
    nest_handoff_history: bool | None = None
    is_enabled: bool | CallbackRef = True

    @model_validator(mode="after")
    def validate_input(self) -> "HandoffRef":
        if self.input_type is not None and self.on_handoff is None:
            raise ValueError("input_type requires on_handoff")
        return self
