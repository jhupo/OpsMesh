from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from opsmesh.contracts import UserIdentity
from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.http import get_application, get_current_user, get_db_session, serialize_record
from opsmesh.users.service import require_owner

from . import service
from .models import CatalogPublication
from .schemas import PublicationView, PublishResourceRequest

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.post("", status_code=201, response_model=PublicationView)
def publish_resource(
    body: PublishResourceRequest,
    request: Request,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    app = get_application(request)
    row = service.publish_resource(db, app.cipher, app.registry, app.settings.data_dir, user, body)
    return serialize_record(row)


@router.get("", response_model=list[PublicationView])
def list_publications(
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
    kind: str | None = None,
    name: str | None = None,
    owner_id: str | None = None,
    version: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = service.discover(
        db,
        user,
        kind=kind,
        name=name,
        owner_id=owner_id,
        version=version,
        offset=offset,
        limit=limit,
    )
    return [serialize_record(row) for row in rows]


@router.get("/{publication_id}", response_model=PublicationView)
def detail(
    publication_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    return serialize_record(service.get(db, user, publication_id))


@router.delete("/{publication_id}", status_code=204)
def withdraw(
    publication_id: str,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> None:
    row = db.get(CatalogPublication, publication_id, with_for_update=True)
    if row is None:
        raise NotFoundError("CatalogPublication not found")
    require_owner(user, row.owner_id)
    row.enabled = False
