from uuid import UUID

from pydantic import BaseModel, Field

CORRECTION_TARGET_PATTERN = "^(task|step|agent|artifact|final_output)$"
CORRECTION_MODE_PATTERN = "^(revise|regenerate|add_missing_work|replace_artifact|stop_work)$"


class TaskCorrectionRequest(BaseModel):
    target_type: str = Field(pattern=CORRECTION_TARGET_PATTERN)
    mode: str = Field(pattern=CORRECTION_MODE_PATTERN)
    instruction: str = Field(min_length=1, max_length=4_000)
    target_id: UUID | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskControlActionRequest(BaseModel):
    action: str = Field(pattern="^(pause|resume|add_instruction|create_correction|cancel)$")
    instruction: str | None = Field(default=None, max_length=4_000)
    reason: str | None = Field(default=None, max_length=1_000)
    enqueue: bool = True
    correction_mode: str | None = Field(default=None, pattern=CORRECTION_MODE_PATTERN)
    target_type: str | None = Field(default=None, pattern=CORRECTION_TARGET_PATTERN)
    target_id: UUID | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class TaskDeliveryDecisionRequest(BaseModel):
    action: str = Field(pattern="^(approve|request_changes|reject)$")
    summary: str = Field(min_length=1, max_length=4_000)
    instruction: str | None = Field(default=None, max_length=4_000)
    finalize: bool = True
    correction_mode: str | None = Field(default=None, pattern=CORRECTION_MODE_PATTERN)
    target_type: str | None = Field(default=None, pattern=CORRECTION_TARGET_PATTERN)
    target_id: UUID | None = None
    override: bool = False
    override_reason: str | None = Field(default=None, max_length=1_000)
    metadata: dict[str, object] = Field(default_factory=dict)
