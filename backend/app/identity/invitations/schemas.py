from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, SecretStr, field_validator

from backend.app.messaging.email.schemas import EmailRecipient
from backend.app.shared.contracts import ORMModel


class UserInvitationRequest(EmailRecipient):
    display_name: str = Field(default="", max_length=120)
    platform_admin: bool = False


class UserInvitationResponse(ORMModel):
    id: UUID
    user_id: UUID
    delivery_status: str
    expires_at: datetime
    sent_at: datetime | None


class AcceptUserInvitationRequest(BaseModel):
    token: SecretStr = Field(min_length=32, max_length=256, repr=False)
    password: SecretStr = Field(min_length=8, max_length=4096, repr=False)
    display_name: str = Field(min_length=1, max_length=120)
    username: str = Field(default="", max_length=80)

    @field_validator("display_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Display name is required")
        return value
