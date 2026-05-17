"""Import all SQLAlchemy models so relationship targets are registered."""

from backend.app.agents.models import AgentProfile
from backend.app.approvals.models import Approval
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.files.models import FileAccessEvent, WorkspaceFile
from backend.app.identity.models import User
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtimes.models import (
    RuntimeCommand,
    RuntimeEvent,
    RuntimeTemplate,
    WorkspaceRuntime,
)
from backend.app.tasks.models import Task, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workspaces.models import Workspace, WorkspaceMember

__all__ = [
    "AgentProfile",
    "AgentRun",
    "AgentTeam",
    "AgentTeamMember",
    "Approval",
    "Artifact",
    "AuditEvent",
    "FileAccessEvent",
    "RunEvent",
    "RuntimeCommand",
    "RuntimeEvent",
    "RuntimeTemplate",
    "Task",
    "TaskStep",
    "User",
    "Workspace",
    "WorkspaceFile",
    "WorkspaceMember",
    "WorkspaceRuntime",
]
