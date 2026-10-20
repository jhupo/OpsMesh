"""Explicit registration of the application-owned PostgreSQL model modules."""

from sqlalchemy import MetaData

from opsmesh.capabilities import models as capabilities
from opsmesh.channels import models as channels
from opsmesh.extensions import models as extensions
from opsmesh.identity.auth import models as authentication
from opsmesh.identity.invitations import models as invitations
from opsmesh.identity.quotas import models as quotas
from opsmesh.identity.users import models as users
from opsmesh.orchestration.configurations import models as configurations
from opsmesh.orchestration.configurations.bindings import models as bindings
from opsmesh.orchestration.definitions import models as definitions
from opsmesh.orchestration.runs import model_operations
from opsmesh.orchestration.runs import models as runs
from opsmesh.orchestration.runtime import models as runtime
from opsmesh.orchestration.sessions import models as sessions
from opsmesh.orchestration.triggers import models as triggers
from opsmesh.platform.approvals import models as approvals
from opsmesh.platform.audit import models as audit
from opsmesh.platform.model_connections import models as model_connections
from opsmesh.platform.operations import models as operations
from opsmesh.platform.policies import models as policies
from opsmesh.resources.artifacts import models as artifacts
from opsmesh.resources.files import models as files
from opsmesh.shared.db import idempotency
from opsmesh.shared.db.base import Base
from opsmesh.temporal.dispatch import models as dispatch


def register_models() -> MetaData:
    """Load every owner explicitly, without discovering arbitrary executable packages."""
    _ = (
        capabilities,
        channels,
        extensions,
        authentication,
        invitations,
        quotas,
        users,
        configurations,
        bindings,
        definitions,
        model_operations,
        runs,
        runtime,
        sessions,
        triggers,
        approvals,
        audit,
        model_connections,
        operations,
        policies,
        artifacts,
        files,
        idempotency,
        dispatch,
    )
    return Base.metadata
