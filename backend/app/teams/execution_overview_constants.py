
from backend.app.orchestration.statuses import ACTIVE_RUN_STATUSES as _ACTIVE_RUN_STATUSES

ACTIVE_RUN_STATUSES = _ACTIVE_RUN_STATUSES
ACTIVE_STEP_STATUSES = {"queued", "running", "blocked"}
DONE_TASK_STATUSES = {"completed", "cancelled", "canceled"}
REASSIGNABLE_SPECIALIST_STEP_STATUSES = {"blocked", "failed"}
RISK_LEVELS = ("critical", "high", "medium", "low")
