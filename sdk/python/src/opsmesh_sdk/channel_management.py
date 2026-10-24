"""Typed conversation operations supplied by channel adapters and trusted hosts."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)

ManagementAction = Literal[
    "conversation",
    "permissions",
    "members",
    "member",
    "restrictions",
    "join_requests",
    "recall",
    "mute",
    "unmute",
    "review_join",
    "remove_member",
    "blacklist",
    "unblacklist",
]
READ_ACTIONS: frozenset[ManagementAction] = frozenset(
    {"conversation", "permissions", "members", "member", "restrictions", "join_requests"}
)


class ManagementValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConversationRef(ManagementValue):
    scope: str = Field(min_length=1, max_length=300)
    kind: Literal["group", "guild"]
    external_id: str = Field(min_length=1, max_length=500)


class OperationBase(ManagementValue):
    conversation: ConversationRef
    reason: str = Field(default="", max_length=1000)


class ConversationQuery(OperationBase):
    action: Literal["conversation", "permissions", "restrictions"]


class ListQuery(OperationBase):
    action: Literal["members", "join_requests"]
    cursor: str = Field(default="", max_length=2000)
    limit: int = Field(default=30, ge=1, le=100)


class MemberQuery(OperationBase):
    action: Literal["member"] = "member"
    member_id: str = Field(min_length=1, max_length=500)


class RecallMessage(OperationBase):
    action: Literal["recall"] = "recall"
    message_id: str = Field(min_length=1, max_length=500)


class MuteMember(OperationBase):
    action: Literal["mute"] = "mute"
    member_id: str = Field(min_length=1, max_length=500)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("Mute expiry requires a timezone")
        return value


class MemberAction(OperationBase):
    action: Literal["unmute", "remove_member", "blacklist", "unblacklist"]
    member_id: str = Field(min_length=1, max_length=500)


class ReviewJoinRequest(OperationBase):
    action: Literal["review_join"] = "review_join"
    member_id: str = Field(min_length=1, max_length=500)
    request_id: str = Field(min_length=1, max_length=2000)
    decision: Literal["approve", "decline"]
    blacklist: bool = False

    @model_validator(mode="after")
    def validate_blacklist(self) -> "ReviewJoinRequest":
        if self.blacklist and self.decision != "decline":
            raise ValueError("Only declined join requests may be blacklisted")
        return self


ManagementRequest = Annotated[
    ConversationQuery
    | ListQuery
    | MemberQuery
    | RecallMessage
    | MuteMember
    | MemberAction
    | ReviewJoinRequest,
    Field(discriminator="action"),
]
MANAGEMENT_REQUEST: TypeAdapter[ManagementRequest] = TypeAdapter(ManagementRequest)


class ActionCapability(ManagementValue):
    action: ManagementAction
    available: bool | None = None
    reason: str = ""
    max_duration_seconds: int | None = None
    max_message_age_seconds: int | None = None
    max_page_size: int | None = None
    fixed_page_size: int | None = None


class ManagementResult(ManagementValue):
    data: dict[str, JsonValue] = Field(default_factory=dict)
    capabilities: list[ActionCapability] = Field(default_factory=list)


class OperationReceipt(ManagementValue):
    id: UUID
    operation: ManagementRequest
    actor_id: UUID
    resource_id: str | None = None
    attempts: int = 0
    expires_at: datetime
    status: Literal[
        "awaiting_approval",
        "pending",
        "executing",
        "succeeded",
        "failed",
        "uncertain",
        "cancelled",
        "expired",
    ]
    result: ManagementResult | None = None
    error_code: str | None = None


class ManagementRejected(Exception):
    """Confirmed rejection; retry only when explicitly marked safe."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code, self.retryable = code, retryable


class ManagementUncertain(Exception):
    """The platform may have applied an operation; never blindly repeat it."""


class ConversationManagement(ABC):
    capabilities: tuple[ActionCapability, ...] = ()

    @abstractmethod
    async def execute(self, request: ManagementRequest) -> ManagementResult: ...


class ManagementAccess(Protocol):
    """A host-injected, authorized capability for system plugins, not an API client."""

    async def submit(self, request: ManagementRequest) -> OperationReceipt: ...
    async def get(self, operation_id: UUID) -> OperationReceipt: ...
