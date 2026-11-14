"""Public conversation contracts, separate from native SDK history."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ConversationView(BaseModel):
    """Expose only conversation metadata owned by the caller."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    deleting: bool
    created_at: datetime
    updated_at: datetime
