from fastapi import APIRouter, Depends

from opsmesh.capabilities.governance.admin_routes import router as capability_governance_router
from opsmesh.capabilities.marketplace.admin_routes import router as marketplace_review_router
from opsmesh.capabilities.plugins.admin_routes import router as plugin_governance_router
from opsmesh.governance.audit.admin_routes import router as audit_router
from opsmesh.governance.costs.admin_routes import router as costs_router
from opsmesh.governance.policies.routes import router as policies_router
from opsmesh.governance.security_events.routes import router as security_events_router
from opsmesh.identity.authorization.admin_dependencies import require_platform_admin
from opsmesh.identity.authorization.admin_routes import router as authorization_router
from opsmesh.identity.invitations.admin_routes import router as invitations_router
from opsmesh.identity.users.admin_routes import router as users_router
from opsmesh.messaging.email.routes import router as mail_router
from opsmesh.platform.announcements.routes import router as announcements_router
from opsmesh.platform.overview.catalog_routes import router as catalog_router
from opsmesh.platform.overview.routes import router as overview_router
from opsmesh.platform.releases.routes import router as releases_router
from opsmesh.platform.settings.routes import router as system_router
from opsmesh.platform.updates.routes import router as updates_router
from opsmesh.runtime.instances.admin_routes import router as runtimes_router
from opsmesh.runtime.instances.lease_admin_routes import router as leases_router
from opsmesh.runtime.operations.admin_dashboard_routes import router as dashboard_router
from opsmesh.runtime.queues.admin_routes import router as queues_router
from opsmesh.runtime.spaces.admin_routes import router as runtime_spaces_router
from opsmesh.runtime.workers.admin_routes import router as workers_router
from opsmesh.workspaces.management.admin_routes import router as management_router
from opsmesh.workspaces.members.admin_routes import router as members_router
from opsmesh.workspaces.projects.admin_routes import router as projects_router

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_platform_admin)],
)
router.include_router(overview_router)
router.include_router(marketplace_review_router)
router.include_router(costs_router)
router.include_router(dashboard_router)
router.include_router(announcements_router)
router.include_router(catalog_router)
router.include_router(capability_governance_router)
router.include_router(plugin_governance_router)
router.include_router(management_router)
router.include_router(members_router)
router.include_router(projects_router)
router.include_router(authorization_router)
router.include_router(audit_router)
router.include_router(workers_router)
router.include_router(runtime_spaces_router)
router.include_router(leases_router)
router.include_router(queues_router)
router.include_router(runtimes_router)
router.include_router(policies_router)
router.include_router(security_events_router)
router.include_router(system_router)
router.include_router(releases_router)
router.include_router(updates_router)
router.include_router(users_router)
router.include_router(mail_router)
router.include_router(invitations_router)
