from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ReplyPreparation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    complete: bool
    delivery_id: UUID | None = None
