from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from backend.app.api.pagination import PageResponse, pagination_params
from backend.app.api.schemas.platform.admin import AdminCatalogResourceResponse
from backend.app.core.db.session import get_db_session
from backend.app.core.pagination import PageParams
from backend.app.domains.platform.admin.catalog import AdminCatalogKind, AdminCatalogService

router = APIRouter()


def _response(resource: object) -> AdminCatalogResourceResponse:
    return AdminCatalogResourceResponse.model_validate(resource, from_attributes=True)


@router.get(
    "/catalog/{kind}",
    response_model=PageResponse[AdminCatalogResourceResponse],
)
async def list_admin_catalog_resources(
    kind: AdminCatalogKind,
    page: PageParams = Depends(pagination_params),
    workspace_id: UUID | None = Query(default=None),
    resource_status: str | None = Query(default=None, alias="status"),
    session: Session = Depends(get_db_session),
) -> PageResponse[AdminCatalogResourceResponse]:
    try:
        resources, total = AdminCatalogService(session).list_resources(
            kind,
            page,
            workspace_id=workspace_id,
            status=resource_status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageResponse(
        items=[_response(resource) for resource in resources],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/catalog/{kind}/{resource_id}",
    response_model=AdminCatalogResourceResponse,
)
async def get_admin_catalog_resource(
    kind: AdminCatalogKind,
    resource_id: UUID,
    workspace_id: UUID | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> AdminCatalogResourceResponse:
    try:
        resource = AdminCatalogService(session).get_resource(
            kind,
            resource_id,
            workspace_id=workspace_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if resource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    return _response(resource)
