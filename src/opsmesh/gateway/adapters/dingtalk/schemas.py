"""Parameters for the declared DingTalk operations, never transport routing."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CardData(Parameters):
    cardParamMap: dict[str, str]


class OpenSpace(Parameters):
    supportForward: bool = True


class CreateCard(Parameters):
    cardTemplateId: str = Field(min_length=1)
    outTrackId: str = Field(min_length=1)
    cardData: CardData
    callbackType: Literal["STREAM", "HTTP"] = "STREAM"
    callbackRouteKey: str | None = None
    imGroupOpenSpaceModel: OpenSpace | None = None
    imRobotOpenSpaceModel: OpenSpace | None = None


class GroupDelivery(Parameters):
    robotCode: str = Field(min_length=1)
    atUserIds: dict[str, str] | None = None
    recipients: list[str] | None = None


class RobotDelivery(Parameters):
    spaceType: Literal["IM_ROBOT"] = "IM_ROBOT"


class DeliverCard(Parameters):
    outTrackId: str = Field(min_length=1)
    userIdType: Literal[1, 2] = 1
    openSpaceId: str = Field(min_length=1)
    imGroupOpenDeliverModel: GroupDelivery | None = None
    imRobotOpenDeliverModel: RobotDelivery | None = None


class UpdateCard(Parameters):
    outTrackId: str = Field(min_length=1)
    cardData: CardData


class StreamCard(Parameters):
    outTrackId: str = Field(min_length=1)
    guid: str = Field(min_length=1)
    key: str = Field(min_length=1)
    content: str
    isFull: bool = True
    isFinalize: bool = False
    isError: bool = False


class CardResponse(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    success: Literal[True]
    result: Any = None


CARD_OPERATIONS: dict[str, tuple[str, str, type[Parameters]]] = {
    "dingtalk.card.create": ("POST", "/v1.0/card/instances", CreateCard),
    "dingtalk.card.deliver": ("POST", "/v1.0/card/instances/deliver", DeliverCard),
    "dingtalk.card.update": ("PUT", "/v1.0/card/instances", UpdateCard),
    "dingtalk.card.stream": ("PUT", "/v1.0/card/streaming", StreamCard),
}
