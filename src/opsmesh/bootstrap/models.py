"""Explicit registration for the current source tree, without legacy discovery."""

from sqlalchemy import MetaData

from opsmesh.agents.catalog import models as catalog
from opsmesh.execution.instances import models as instances
from opsmesh.execution.runs import models as runs
from opsmesh.execution.runtime import models as runtime
from opsmesh.execution.sessions import models as model_sessions
from opsmesh.gateway import models as gateway
from opsmesh.platform.audit import models as audit
from opsmesh.platform.billing import models as billing
from opsmesh.platform.evaluations import models as evaluations
from opsmesh.resources.directories import models as resources
from opsmesh.shared.db.base import Base
from opsmesh.temporal.dispatch import models as dispatch
from opsmesh.users import models as users
from opsmesh.users.auth import models as authentication
from opsmesh.users.invitations import models as invitations
from opsmesh.users.quotas import models as quotas
from opsmesh.workflows import models as workflows
from opsmesh.workflows.instances import models as workflow_instances
from opsmesh.workflows.runs import models as workflow_runs
from opsmesh.workflows.triggers import models as triggers


def register_models() -> MetaData:
    _ = (
        catalog,
        billing,
        evaluations,
        triggers,
        runs,
        instances,
        model_sessions,
        gateway,
        runtime,
        resources,
        users,
        authentication,
        invitations,
        quotas,
        workflows,
        workflow_instances,
        workflow_runs,
        audit,
        dispatch,
    )
    return Base.metadata
