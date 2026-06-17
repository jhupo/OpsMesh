from __future__ import annotations

from backend.app.orchestration.statuses import ACTIVE_RUN_STATUSES as _ACTIVE_RUN_STATUSES

ACTIVE_RUN_STATUSES = _ACTIVE_RUN_STATUSES
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
