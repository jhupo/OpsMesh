from fastapi import APIRouter

from backend.app.capabilities.marketplace.resource_routes import (
    router as marketplace_resource_router,
)
from backend.app.capabilities.marketplace.routes.catalog import router as talent_catalog_router
from backend.app.capabilities.marketplace.routes.hiring import router as talent_hiring_router
from backend.app.capabilities.marketplace.routes.install import router as talent_install_router
from backend.app.capabilities.marketplace.routes.publish import router as talent_publish_router
from backend.app.capabilities.marketplace.routes.recommendation import (
    router as talent_recommendation_router,
)
from backend.app.capabilities.marketplace.routes.review import router as talent_review_router

router = APIRouter(tags=["talent-marketplace"])
router.include_router(marketplace_resource_router)
router.include_router(talent_catalog_router)
router.include_router(talent_publish_router)
router.include_router(talent_hiring_router)
router.include_router(talent_recommendation_router)
router.include_router(talent_install_router)
router.include_router(talent_review_router)
