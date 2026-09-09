"""Import all SQLAlchemy models so relationship targets are registered."""

from backend.app.admin.models import PlatformPolicy, PlatformPolicyEvent
from backend.app.agent_messages.models import AgentMessage, AgentMessageThread
from backend.app.agent_runtime.sessions import (
    PersistentAgentSession,
    PersistentAgentSessionItem,
)
from backend.app.agents.models import AgentProfile, AgentProfileVersion
from backend.app.approvals.models import Approval, PendingToolInvocation
from backend.app.artifacts.models import Artifact
from backend.app.audit.models import AuditEvent, AuditIntegrityCheck
from backend.app.capabilities.models import (
    Capability,
    CapabilityResource,
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    McpToolCallLog,
    Skill,
    ToolGroup,
    WorkspaceSkillInstall,
)
from backend.app.costs.models import ModelPricingRule, ModelUsageRecord, WorkspaceCostBudget
from backend.app.domains.models import DomainItem, DomainProject, ReviewComment, RevisionRequest
from backend.app.exports.models import WorkspaceExportJob
from backend.app.files.models import FileAccessEvent, WorkspaceFile
from backend.app.identity.models import User, UserAPIToken
from backend.app.marketplace.models import (
    MarketplaceListing,
    TalentListing,
    TalentListingReview,
    WorkspaceAgentInstall,
    WorkspaceMarketplaceInstall,
)
from backend.app.memory.models import (
    WorkspaceMemoryConfiguration,
    WorkspaceMemoryEmbeddingEvent,
    WorkspaceMemoryEntry,
    WorkspaceMemoryLifecycleEvent,
    WorkspaceMemoryRetrievalEvent,
    WorkspaceMemoryVersion,
)
from backend.app.model_providers.models import ModelProviderCredential
from backend.app.notifications.models import WorkspaceNotification
from backend.app.operations.models import WorkerHeartbeat, WorkerLease, WorkerNode
from backend.app.planning.models import TaskPlanningAttempt
from backend.app.projects.models import (
    AgentRunProjectIOState,
    AgentRunProjectSnapshot,
    WorkspaceProject,
    WorkspaceProjectConfigurationVersion,
    WorkspaceProjectFile,
    WorkspaceProjectOutput,
)
from backend.app.runs.models import AgentRun, AgentRunStateSnapshot, RunEvent
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
    "AgentRunProjectIOState",
    "AgentRunProjectSnapshot",
    "AgentRunStateSnapshot",
    "AgentTeam",
    "AgentTeamMember",
    "Approval",
    "PendingToolInvocation",
    "Artifact",
    "AuditEvent",
    "AuditIntegrityCheck",
    "Capability",
    "CapabilityResource",
    "DomainItem",
    "DomainProject",
    "FileAccessEvent",
    "LocalFileReference",
    "McpCredentialReference",
    "McpServer",
    "McpToolAllowlist",
    "McpToolCallLog",
    "MarketplaceListing",
    "ModelPricingRule",
    "ModelUsageRecord",
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
    "WorkspaceCostBudget",
    "WorkspaceInvite",
    "WorkspaceAgentInstall",
    "WorkspaceExportJob",
    "WorkspaceFile",
    "WorkspaceMember",
    "WorkspaceMemoryConfiguration",
    "WorkspaceMemoryEmbeddingEvent",
    "WorkspaceMemoryEntry",
    "WorkspaceMemoryLifecycleEvent",
    "WorkspaceMemoryRetrievalEvent",
    "WorkspaceMemoryVersion",
    "WorkspaceMarketplaceInstall",
    "WorkspaceNotification",
    "WorkspaceProject",
    "WorkspaceProjectConfigurationVersion",
    "WorkspaceProjectFile",
    "WorkspaceProjectOutput",
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
