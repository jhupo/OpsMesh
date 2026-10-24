from typing import Any

from fastapi import APIRouter, Depends, File, Request
from opsmesh_sdk import UserIdentity
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.shared.errors import NotFoundError
from opsmesh.shared.http import get_application, get_current_user, get_db_session, serialize_record
from opsmesh.users.service import require_admin

from .models import Plugin
from .packages import install
from .schemas import PluginView, UpdatePluginRequest

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.post("", status_code=201, response_model=PluginView)
def upload(
    request: Request,
    content: bytes = File(),
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_admin(user)
    app = get_application(request)
    row = install(
        db, app.settings.data_dir, user.user_id, content, app.settings.package_limit_bytes
    )
    return serialize_record(row)


@router.get("", response_model=list[PluginView])
def listing(
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> list[dict[str, Any]]:
    require_admin(user)
    return [
        serialize_record(row)
        for row in db.scalars(select(Plugin).order_by(Plugin.created_at.desc()).limit(200))
    ]


@router.patch("/{plugin_id}", response_model=PluginView)
def update_plugin(
    plugin_id: str,
    body: UpdatePluginRequest,
    user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db_session, scope="function"),
) -> dict[str, Any]:
    require_admin(user)
    row = db.get(Plugin, plugin_id, with_for_update=True)
    if row is None:
        raise NotFoundError("Plugin not found")
    row.enabled = body.enabled
    return serialize_record(row)
