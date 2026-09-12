"""Import all SQLAlchemy models so relationship targets are registered."""

from backend.app.core.admin.models import PlatformPolicy, PlatformPolicyEvent
from backend.app.core.admin.updates.models import (
    PlatformInstallation,
    PlatformUpdateEvent,
    PlatformUpdateJob,
)
from backend.app.core.identity.models import User, UserAPIToken
from backend.app.core.integrations.webhooks.models import (
    WebhookDeliveryAttempt,
    WebhookSubscription,
)
from backend.app.core.security.models import SecurityEvent
from backend.app.domains.agents.memory.models import (
    WorkspaceMemoryConfiguration,
    WorkspaceMemoryEmbeddingEvent,
    WorkspaceMemoryEntry,
    WorkspaceMemoryLifecycleEvent,
    WorkspaceMemoryRetrievalEvent,
    WorkspaceMemoryVersion,
)
from backend.app.domains.agents.messages.models import AgentMessage, AgentMessageThread
from backend.app.domains.agents.models import AgentProfile, AgentProfileVersion
from backend.app.domains.agents.providers.credentials.models import ModelProviderCredential
from backend.app.domains.agents.runtime.sessions import (
    PersistentAgentSession,
    PersistentAgentSessionItem,
)
from backend.app.domains.capabilities.marketplace.models import (
    MarketplaceListing,
    TalentListing,
    TalentListingReview,
    WorkspaceAgentInstall,
    WorkspaceMarketplaceInstall,
)
from backend.app.domains.capabilities.models import (
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
from backend.app.domains.orchestration.approvals.models import Approval, PendingToolInvocation
from backend.app.domains.orchestration.models import (
    OrchestrationDefinition,
    OrchestrationRevision,
    SubworkflowInvocation,
)
from backend.app.domains.orchestration.runs.models import AgentRun, AgentRunStateSnapshot, RunEvent
from backend.app.domains.orchestration.tasks.models import (
    Task,
    TaskEventOutbox,
    TaskMessage,
    TaskStep,
    TaskTransfer,
)
from backend.app.domains.orchestration.workflows.planning.attempt_models import TaskPlanningAttempt
from backend.app.domains.workspace.domains.models import (
    DomainItem,
    DomainProject,
    ReviewComment,
    RevisionRequest,
)
from backend.app.domains.workspace.projects.export_models import WorkspaceExportJob
from backend.app.domains.workspace.projects.models import (
    AgentRunProjectIOState,
    AgentRunProjectSnapshot,
    WorkspaceProject,
    WorkspaceProjectConfigurationVersion,
    WorkspaceProjectFile,
    WorkspaceProjectOutput,
)
from backend.app.domains.workspace.storage.artifact_models import Artifact
from backend.app.domains.workspace.storage.models import FileAccessEvent, WorkspaceFile
from backend.app.domains.workspace.teams.models import AgentTeam, AgentTeamMember
from backend.app.domains.workspace.tenants.models import (
    Workspace,
    WorkspaceInvite,
    WorkspaceMember,
    WorkspaceQuota,
    WorkspaceReservation,
)
from backend.app.observability.audit_models import AuditEvent, AuditIntegrityCheck
from backend.app.observability.cost_models import (
    ModelPricingRule,
    ModelUsageRecord,
    WorkspaceCostBudget,
)
from backend.app.observability.notification_models import WorkspaceNotification
from backend.app.runtime.environment.models import (
    RuntimeCommand,
    RuntimeEvent,
    RuntimeLease,
    RuntimeTemplate,
    WorkspaceRuntime,
)
from backend.app.runtime.environment.spaces.models import (
    RuntimeSpace,
    RuntimeSpaceBinding,
    RuntimeSpaceEvent,
    RuntimeSpaceQuota,
    RuntimeSpaceReservation,
)
from backend.app.runtime.operations.models import WorkerHeartbeat, WorkerLease, WorkerNode
from backend.app.runtime.self_hosted.models import (
    LocalFileReference,
    RuntimeCredential,
    RuntimeEnrollmentToken,
    SelfHostedArtifactUpload,
    SelfHostedJobClaim,
    SelfHostedMcpJob,
    SelfHostedWorker,
)
from backend.app.runtime.workers.scheduling.models import (
    WorkspaceScheduledJob,
    WorkspaceScheduledJobEvent,
)

__all__ = [
    "PlatformInstallation",
    "PlatformUpdateEvent",
    "PlatformUpdateJob",
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
    "OrchestrationDefinition",
    "OrchestrationRevision",
    "SubworkflowInvocation",
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
    "TaskTransfer",
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
