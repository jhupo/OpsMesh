from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from backend.app.orchestration.scheduler_team_capacity import (
    TeamMemberCapacityResolver,
    manager_capacity_context,
)
from backend.app.tasks.models import TaskStep
from backend.app.teams.models import AgentTeam


def test_team_capacity_rejects_mixed_workspace_batch_before_querying() -> None:
    session = Mock(spec=Session)
    steps = [TaskStep(workspace_id=uuid4()), TaskStep(workspace_id=uuid4())]
    with pytest.raises(ValueError, match="one workspace"):
        TeamMemberCapacityResolver(session).contexts(steps)
    session.scalars.assert_not_called()


def test_unassigned_manager_step_does_not_create_null_agent_capacity() -> None:
    team = AgentTeam(manager_agent_profile_id=None)
    step = TaskStep(assigned_agent_profile_id=None, work_package_id="manager-planning")
    assert manager_capacity_context(team, step, {}) is None
