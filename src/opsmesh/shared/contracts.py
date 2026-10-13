from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FromAttributesContract(BaseModel):
    """Base contract for models serialized from persisted domain objects."""

    model_config = ConfigDict(from_attributes=True)


class TimestampedContract(FromAttributesContract):
    """Persisted contract carrying the standard audit timestamps."""

    id: UUID
    created_at: datetime
    updated_at: datetime
