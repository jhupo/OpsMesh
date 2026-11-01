"""Self-service provider credentials and model profiles scoped to the authenticated user."""

from fastapi import APIRouter, Request
from pydantic import Field, SecretStr

from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.models.api import ModelProfile
from opsmesh.modules.models.credentials import ModelCredential
from opsmesh.modules.models.management import DefaultModel

from ..security import CurrentUser, get_application

router = APIRouter(prefix="/me", tags=["personal models"])


class CreateCredential(OpsMeshContract):
    name: str = Field(min_length=1, max_length=100)
    secret: SecretStr


class RotateCredential(OpsMeshContract):
    secret: SecretStr
    expected_revision: int = Field(ge=1)


class CredentialAvailability(OpsMeshContract):
    enabled: bool
    expected_revision: int = Field(ge=1)


class PersonalModelUpdate(OpsMeshContract):
    profile: ModelProfile
    expected_revision: int = Field(ge=1)


class DefaultModelUpdate(OpsMeshContract):
    reference: str | None
    expected_revision: int = Field(ge=0)


@router.post("/model-credentials", status_code=201)
async def create_credential(
    body: CreateCredential, request: Request, user: CurrentUser
) -> ModelCredential:
    return await get_application(request).services.model_credentials.create(
        body.name, body.secret, actor_id=user.id
    )


@router.get("/model-credentials")
async def credentials(request: Request, user: CurrentUser) -> list[ModelCredential]:
    return await get_application(request).services.model_credentials.list(actor_id=user.id)


@router.put("/model-credentials/{identifier}")
async def rotate(
    identifier: str, body: RotateCredential, request: Request, user: CurrentUser
) -> ModelCredential:
    return await get_application(request).services.model_credentials.rotate(
        identifier, body.secret, body.expected_revision, actor_id=user.id
    )


@router.put("/model-credentials/{identifier}/availability")
async def credential_availability(
    identifier: str, body: CredentialAvailability, request: Request, user: CurrentUser
) -> ModelCredential:
    return await get_application(request).services.model_credentials.set_enabled(
        identifier, body.enabled, body.expected_revision, actor_id=user.id
    )


@router.get("/models")
async def models(request: Request, user: CurrentUser) -> list[ModelProfile]:
    return await get_application(request).services.model_management.personal_profiles(
        actor_id=user.id
    )


@router.post("/models", status_code=201)
async def create_model(body: ModelProfile, request: Request, user: CurrentUser) -> ModelProfile:
    return await get_application(request).services.model_management.save_personal(
        body, actor_id=user.id
    )


@router.put("/models")
async def update_model(
    body: PersonalModelUpdate, request: Request, user: CurrentUser
) -> ModelProfile:
    return await get_application(request).services.model_management.save_personal(
        body.profile, actor_id=user.id, expected_revision=body.expected_revision
    )


@router.get("/models/default")
async def default_model(request: Request, user: CurrentUser) -> DefaultModel:
    return await get_application(request).services.model_management.default(
        actor_id=user.id, personal=True
    )


@router.put("/models/default")
async def set_default(
    body: DefaultModelUpdate, request: Request, user: CurrentUser
) -> DefaultModel:
    return await get_application(request).services.model_management.set_default(
        body.reference, body.expected_revision, actor_id=user.id, personal=True
    )
