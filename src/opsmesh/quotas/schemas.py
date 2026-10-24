from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator

from opsmesh.shared.schemas import ReasoningEffort, RecordView, RequestSchema, ResourceView


class UpdateQuotaRequest(RequestSchema):
    monthly_tokens: int = Field(ge=0)
    storage_bytes: int = Field(ge=0)
    concurrent_tasks: int = Field(ge=1, le=100)


class CreateModelConnectionRequest(RequestSchema):
    name: str = Field(min_length=1, max_length=100)
    api_key: SecretStr = Field(min_length=1, max_length=10000)
    base_url: str = Field(
        default="https://api.openai.com/v1", pattern=r"^https://", max_length=2000
    )
    api_mode: Literal["responses", "chat_completions"] = "responses"
    model_name: str = Field(min_length=1, max_length=200)
    reasoning_effort: ReasoningEffort = "medium"
    is_default: bool = False

    @field_validator("base_url")
    @classmethod
    def valid_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.port not in {None, 443}
        ):
            raise ValueError(
                "Model endpoint must use HTTPS without URL credentials, query or fragment"
            )
        return value.rstrip("/")


class SettleTokenReservationRequest(RequestSchema):
    actual: int = Field(ge=0)


class QuotaLimitsView(ResourceView):
    user_id: str
    monthly_tokens: int
    storage_bytes: int
    concurrent_tasks: int


class TokenBudgetView(ResourceView):
    user_id: str
    period: str
    consumed: int
    reserved: int


class UserQuotaView(ResourceView):
    limits: QuotaLimitsView
    budgets: list[TokenBudgetView]
    storage_used_bytes: int


class TokenReservationView(RecordView):
    user_id: str
    task_id: str
    period: str
    amount: int
    actual: int | None
    status: str


class ModelConnectionView(RecordView):
    owner_id: str
    name: str
    base_url: str
    api_mode: str
    enabled: bool
    model_name: str | None
    reasoning_effort: ReasoningEffort
    is_default: bool


class UpdateModelConnectionRequest(RequestSchema):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=10000)
    base_url: str | None = Field(default=None, pattern=r"^https://", max_length=2000)
    api_mode: Literal["responses", "chat_completions"] | None = None
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    reasoning_effort: ReasoningEffort | None = None
    is_default: bool | None = None
    enabled: bool | None = None

    @field_validator("base_url")
    @classmethod
    def valid_endpoint(cls, value: str | None) -> str | None:
        return CreateModelConnectionRequest.valid_endpoint(value) if value is not None else None
