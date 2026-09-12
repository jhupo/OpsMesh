from fastapi import APIRouter

from backend.app.api.routes.capabilities.marketplace_resources import (
    router as marketplace_resource_router,
)
from backend.app.api.routes.capabilities.talent_catalog import (
    router as talent_catalog_router,
)
from backend.app.api.routes.capabilities.talent_hiring import router as talent_hiring_router
from backend.app.api.routes.capabilities.talent_install import (
    router as talent_install_router,
)
from backend.app.api.routes.capabilities.talent_publish import (
    router as talent_publish_router,
)
from backend.app.api.routes.capabilities.talent_recommendation import (
    router as talent_recommendation_router,
)
from backend.app.api.routes.capabilities.talent_review import router as talent_review_router

router = APIRouter(tags=["talent-marketplace"])
router.include_router(marketplace_resource_router)
router.include_router(talent_catalog_router)
router.include_router(talent_publish_router)
router.include_router(talent_hiring_router)
router.include_router(talent_recommendation_router)
router.include_router(talent_install_router)
router.include_router(talent_review_router)
