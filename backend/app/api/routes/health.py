from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from backend.app.core.config import Settings

router = APIRouter()


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    service: str = Field(examples=["chaincloud-backend"])
    environment: str = Field(examples=["local"])
    request_id: str | None = Field(default=None, examples=["01J8G7Y4R7D6C6K4A1RSPJ7J7P"])


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    settings: Settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        environment=settings.environment,
        request_id=getattr(request.state, "request_id", None),
    )

