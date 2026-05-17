"""Import all SQLAlchemy models so relationship targets are registered."""

from backend.app.agents.models import AgentProfile
from backend.app.approvals.models import Approval
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent
from backend.app.capabilities.models import (
    Capability,
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    McpToolCallLog,
    Skill,
    ToolGroup,
    WorkspaceSkillInstall,
)
from backend.app.domains.models import DomainItem, DomainProject, ReviewComment, RevisionRequest
from backend.app.files.models import FileAccessEvent, WorkspaceFile
from backend.app.identity.models import User
from backend.app.operations.models import WorkerHeartbeat
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtimes.models import (
    RuntimeCommand,
    RuntimeEvent,
    RuntimeTemplate,
    WorkspaceRuntime,
)
from backend.app.self_hosted.models import (
    LocalFileReference,
    RuntimeCredential,
    RuntimeEnrollmentToken,
    SelfHostedArtifactUpload,
    SelfHostedJobClaim,
    SelfHostedWorker,
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
    "Capability",
    "DomainItem",
    "DomainProject",
    "FileAccessEvent",
    "LocalFileReference",
    "McpCredentialReference",
    "McpServer",
    "McpToolAllowlist",
    "McpToolCallLog",
    "ReviewComment",
    "RevisionRequest",
    "RunEvent",
    "RuntimeCommand",
    "RuntimeCredential",
    "RuntimeEnrollmentToken",
    "RuntimeEvent",
    "RuntimeTemplate",
    "Skill",
    "SelfHostedArtifactUpload",
    "SelfHostedJobClaim",
    "SelfHostedWorker",
    "Task",
    "TaskStep",
    "ToolGroup",
    "User",
    "Workspace",
    "WorkspaceFile",
    "WorkspaceMember",
    "WorkspaceRuntime",
    "WorkspaceSkillInstall",
    "WorkerHeartbeat",
]
