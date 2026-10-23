"""Register the current feature APIs."""

from fastapi import FastAPI

from opsmesh.agents.catalog.routes import router as catalog
from opsmesh.execution.instances.routes import router as instances
from opsmesh.execution.runs.observability_routes import router as run_observability
from opsmesh.execution.runs.routes import router as runs
from opsmesh.execution.runtime.routes import router as runtime
from opsmesh.execution.sessions.routes import router as sessions
from opsmesh.platform.audit import routes as audit
from opsmesh.platform.billing.routes import router as billing
from opsmesh.platform.evaluations.routes import router as evaluations
from opsmesh.platform.health.routes import router as health
from opsmesh.resources.directories.routes import router as resources
from opsmesh.users.auth.routes import router as authentication
from opsmesh.users.invitations.routes import router as invitations
from opsmesh.users.quotas.routes import router as quotas
from opsmesh.users.routes import router as users
from opsmesh.workflows.instances.routes import router as workflow_instances
from opsmesh.workflows.routes import router as workflows
from opsmesh.workflows.runs.observability_routes import router as workflow_observability
from opsmesh.workflows.runs.routes import router as workflow_runs
from opsmesh.workflows.triggers.routes import router as triggers


def register_routers(app: FastAPI, *, prefix: str) -> None:
    for router in (
        catalog,
        billing,
        evaluations,
        triggers,
        authentication,
        invitations,
        users,
        quotas,
        workflow_instances,
        workflow_runs,
        workflow_observability,
        workflows,
        resources,
        instances,
        runs,
        run_observability,
        sessions,
        runtime,
        health,
        audit.router,
    ):
        app.include_router(router, prefix=prefix)
