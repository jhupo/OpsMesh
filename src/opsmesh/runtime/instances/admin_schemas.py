from pydantic import (
    BaseModel,
)


class AdminForceStopRuntimeRequest(BaseModel):
    reason: str = "Force stopped by platform admin"
