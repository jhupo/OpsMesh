from __future__ import annotations

from backend.app.orchestration.statuses import ACTIVE_RUN_STATUSES as _ACTIVE_RUN_STATUSES

ACTIVE_RUN_STATUSES = _ACTIVE_RUN_STATUSES
COMPLETED_STEP_STATUSES = {"completed", "cancelled", "canceled"}
TEAM_EXECUTION_LOOP_WINDOW_SECONDS = 60
TEAM_RUNTIME_DEFAULT_LOOP_INTERVAL_SECONDS = 300
