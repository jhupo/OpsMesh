from __future__ import annotations

from backend.app.orchestration.statuses import ORCHESTRATION_ACTIVE_RUN_STATUSES

ACTIVE_RUN_STATUSES = ORCHESTRATION_ACTIVE_RUN_STATUSES
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
