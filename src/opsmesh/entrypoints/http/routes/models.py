"""Versioned model profiles containing credential references only."""

from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from pydantic import Field

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.models.api import ModelProfile
from opsmesh.modules.resource_contracts import ResourceReference, VersionAvailability

from ..security import (
    CurrentUser,
    authorize,
    get_application,
    reject_secret_values,
    validate_secret_reference,
)

router = APIRouter(prefix="/models", tags=["models"])


class UpdateModelRequest(OpsMeshContract):
    profile: ModelProfile
    expected_revision: int = Field(ge=1)


def validate_profile(profile: ModelProfile) -> None:
    validate_secret_reference(profile.secret_ref)
    reject_secret_values(profile.settings)
    if profile.base_url:
        url = urlsplit(profile.base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
        ):
            raise OpsMeshError(
                "CONFIG_INVALID", "Model endpoint must be HTTP(S) without embedded credentials"
            )


@router.post("", status_code=201)
async def create_profile(body: ModelProfile, request: Request, user: CurrentUser) -> ModelProfile:
    await authorize(request, user, "model:manage", body.id)
    validate_profile(body)
    return await get_application(request).services.model_management.create(body, actor_id=user.id)


@router.get("")
async def profiles(request: Request, user: CurrentUser) -> list[ModelProfile]:
    await authorize(request, user, "model:read", "*")
    return await get_application(request).services.model_management.list(actor_id=user.id)


class ModelAvailabilityRequest(OpsMeshContract):
    enabled: bool
    expected_revision: int = Field(ge=0)
    reason: str = Field(default="", max_length=2000)


@router.get("/{profile_id}/versions")
async def profile_versions(
    profile_id: str, request: Request, user: CurrentUser
) -> tuple[ModelProfile, ...]:
    return await get_application(request).services.model_management.versions(
        profile_id, actor_id=user.id
    )


@router.get("/{profile_id}/availability")
async def profile_availability(
    profile_id: str, request: Request, user: CurrentUser
) -> VersionAvailability:
    return await get_application(request).services.model_management.availability(
        profile_id, actor_id=user.id
    )


@router.put("/{profile_id}/availability")
async def set_profile_availability(
    profile_id: str, body: ModelAvailabilityRequest, request: Request, user: CurrentUser
) -> VersionAvailability:
    return await get_application(request).services.model_management.set_enabled(
        profile_id,
        body.enabled,
        actor_id=user.id,
        expected_revision=body.expected_revision,
        reason=body.reason,
    )


@router.get("/{profile_id}/references")
async def model_references(
    profile_id: str, request: Request, user: CurrentUser
) -> tuple[ResourceReference, ...]:
    return await get_application(request).services.model_management.references(
        profile_id, actor_id=user.id
    )


@router.get("/{profile_id:path}")
async def profile(profile_id: str, request: Request, user: CurrentUser) -> ModelProfile:
    await authorize(request, user, "model:read", profile_id)
    return await get_application(request).services.model_management.get(
        profile_id, actor_id=user.id
    )


@router.put("/{profile_id:path}")
async def update_profile(
    profile_id: str, body: UpdateModelRequest, request: Request, user: CurrentUser
) -> ModelProfile:
    await authorize(request, user, "model:manage", profile_id)
    if profile_id != body.profile.id:
        raise OpsMeshError("INVALID_REQUEST", "Model identifier cannot change")
    validate_profile(body.profile)
    return await get_application(request).services.model_management.update(
        body.profile, body.expected_revision, actor_id=user.id
    )
