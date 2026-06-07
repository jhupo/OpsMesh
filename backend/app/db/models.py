"""Import all SQLAlchemy models so relationship targets are registered."""

from backend.app.admin.models import PlatformPolicy, PlatformPolicyEvent
from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agent_runtime.sessions import (
    PersistentAgentSession,
    PersistentAgentSessionItem,
)
from backend.app.agents.models import AgentProfile, AgentProfileVersion
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
from backend.app.exports.models import WorkspaceExportJob
from backend.app.files.models import FileAccessEvent, WorkspaceFile
from backend.app.identity.models import User, UserAPIToken
from backend.app.marketplace.models import (
    TalentListing,
    TalentListingReview,
    WorkspaceAgentInstall,
)
from backend.app.memory.models import WorkspaceMemoryEntry
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.notifications.models import WorkspaceNotification
from backend.app.operations.models import WorkerHeartbeat, WorkerLease, WorkerNode
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.runs.models import AgentRun, RunEvent
from backend.app.runtime_spaces.models import (
    RuntimeSpace,
    RuntimeSpaceBinding,
    RuntimeSpaceEvent,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.runtimes.models import (
    RuntimeCommand,
    RuntimeEvent,
    RuntimeLease,
    RuntimeTemplate,
    WorkspaceRuntime,
)
from backend.app.scheduled_jobs.models import (
    WorkspaceScheduledJob,
    WorkspaceScheduledJobEvent,
)
from backend.app.security.models import SecurityEvent
from backend.app.self_hosted.models import (
    LocalFileReference,
    RuntimeCredential,
    RuntimeEnrollmentToken,
    SelfHostedArtifactUpload,
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)
from backend.app.tasks.models import Task, TaskEventOutbox, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.webhooks.models import WebhookDeliveryAttempt, WebhookSubscription
from backend.app.workspaces.models import (
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
    WorkspaceQuota,
    WorkspaceReservation,
)

__all__ = [
    "AgentProfile",
    "AgentProfileVersion",
    "AgentMessage",
    "AgentMessageThread",
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
    "ModelProviderCredential",
    "PersistentAgentSession",
    "PersistentAgentSessionItem",
    "PlatformPolicy",
    "PlatformPolicyEvent",
    "ReviewComment",
    "RevisionRequest",
    "RunEvent",
    "RuntimeSpace",
    "RuntimeSpaceBinding",
    "RuntimeSpaceEvent",
    "RuntimeSpaceQuota",
    "RuntimeSpaceReservation",
    "SecurityEvent",
    "RuntimeCommand",
    "RuntimeCredential",
    "RuntimeEnrollmentToken",
    "RuntimeEvent",
    "RuntimeLease",
    "RuntimeTemplate",
    "Skill",
    "SelfHostedArtifactUpload",
    "SelfHostedJobClaim",
    "SelfHostedMcpJob",
    "SelfHostedWorker",
    "Task",
    "TaskEventOutbox",
    "TaskMessage",
    "TaskPlanningAttempt",
    "TaskStep",
    "TalentListing",
    "TalentListingReview",
    "ToolGroup",
    "User",
    "UserAPIToken",
    "Workspace",
    "WorkspaceInvite",
    "WorkspaceAgentInstall",
    "WorkspaceExportJob",
    "WorkspaceFile",
    "WorkspaceMember",
    "WorkspaceMemoryEntry",
    "WorkspaceNotification",
    "WorkspaceQuota",
    "WorkspaceReservation",
    "WorkspaceRuntime",
    "WorkspaceScheduledJob",
    "WorkspaceScheduledJobEvent",
    "WorkspaceSkillInstall",
    "WorkerHeartbeat",
    "WorkerLease",
    "WorkerNode",
    "WebhookDeliveryAttempt",
    "WebhookSubscription",
]
