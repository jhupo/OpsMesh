"""Explicitly register the ORM models owned by the current application."""

from sqlalchemy import MetaData

from opsmesh.capabilities.models import (
    CapabilityDefinition,
    CapabilityPackage,
    CapabilityVersion,
)
from opsmesh.governance.approvals.models import ApprovalRequest
from opsmesh.governance.audit.models import (
    AuditAppendReceipt,
    AuditChainHead,
    AuditEvent,
    AuditVerification,
)
from opsmesh.governance.costs.models import (
    CostBudget,
    CostBudgetReservation,
    CostBudgetWindow,
    CostPricingRuleVersion,
    CostUsageRecord,
)
from opsmesh.governance.security_events.models import SecurityEvent
from opsmesh.identity.auth.models import LoginSession, UserCredential
from opsmesh.identity.invitations.models import AccountInvitation
from opsmesh.identity.quotas.models import (
    StorageReservation,
    TokenReservation,
    UserQuotaLimit,
    UserStorageAccount,
    UserTokenAccount,
)
from opsmesh.identity.users.models import UserProfile
from opsmesh.platform.mail.models import MailIntent, SmtpConfiguration
from opsmesh.resources.artifacts.models import ResourceArtifact
from opsmesh.resources.files.models import (
    ResourceDirectory,
    ResourceFile,
    ResourceFileContent,
    ResourceFileGrant,
    ResourceFileVersion,
    ResourceRuntimePolicy,
)
from opsmesh.shared.db.base import Base
from opsmesh.shared.db.idempotency import CreationReceipt
from opsmesh.temporal.dispatch.models import TemporalOutboxEntry
from opsmesh.workspaces.capacity.models import (
    WorkspaceCapacityAccount,
    WorkspaceCapacityReservation,
)
from opsmesh.workspaces.credentials.models import WorkspaceCredential
from opsmesh.workspaces.members.models import WorkspaceInvitation, WorkspaceMember
from opsmesh.workspaces.model_providers.models import ModelProviderConnection
from opsmesh.workspaces.models import Workspace
from opsmesh.workspaces.notifications.models import (
    NotificationPreference,
    WorkspaceNotification,
)
from opsmesh.workspaces.projects.members.models import ProjectInvitation, ProjectMember
from opsmesh.workspaces.projects.models import Project


def register_models() -> MetaData:
    """Explicitly register the tables owned by the current application."""
    _ = (
        ApprovalRequest,
        AuditAppendReceipt,
        AuditChainHead,
        AuditEvent,
        AuditVerification,
        AccountInvitation,
        CapabilityDefinition,
        CapabilityPackage,
        CapabilityVersion,
        CostBudget,
        CostBudgetReservation,
        CostBudgetWindow,
        CostPricingRuleVersion,
        CostUsageRecord,
        CreationReceipt,
        LoginSession,
        MailIntent,
        ModelProviderConnection,
        NotificationPreference,
        Project,
        ProjectInvitation,
        ProjectMember,
        ResourceArtifact,
        ResourceDirectory,
        ResourceFile,
        ResourceFileContent,
        ResourceFileGrant,
        ResourceFileVersion,
        ResourceRuntimePolicy,
        SecurityEvent,
        SmtpConfiguration,
        StorageReservation,
        TemporalOutboxEntry,
        TokenReservation,
        UserCredential,
        UserProfile,
        UserQuotaLimit,
        UserStorageAccount,
        UserTokenAccount,
        Workspace,
        WorkspaceCapacityAccount,
        WorkspaceCapacityReservation,
        WorkspaceCredential,
        WorkspaceInvitation,
        WorkspaceMember,
        WorkspaceNotification,
    )
    return Base.metadata
