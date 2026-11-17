"""Deliver one bounded Markdown stream to one native DingTalk AI card."""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
from alibabacloud_dingtalk.card_1_0 import models
from dingtalk_stream import ChatbotMessage
from dingtalk_stream.card_replier import AICardStatus, CardReplier

from ..receipts import Delivery
from ..sending import MAX_STREAM_BYTES, close_source
from .client import DingTalkClient
from .errors import DingTalkSendError
from .events import DingTalkMessageEvent

if TYPE_CHECKING:
    from .channel import DingTalkChannel


class DingTalkCardStream:
    """Keep native SDK card operations within the caller's delivery scope."""

    def __init__(
        self, client: DingTalkClient, channel: "DingTalkChannel"
    ) -> None:
        """Borrow the connection without retaining a background task."""
        self.client = client
        self.channel = channel
        self.settings = channel.settings.card
        self.card_id: str | None = None
        self.token = ""
        self.content = ""
        self._last_update = 0.0
        self._finalized = False
        self.delivery = None
        self.message_id = ""

    async def send(
        self,
        event: DingTalkMessageEvent,
        source: AsyncIterator[str],
        *,
        delivery: Delivery | None = None,
        message_id: str | None = None,
    ) -> dict:
        """Close the source and finalize the same card on every exit path."""
        self.delivery = delivery or Delivery()
        self.delivery.check_owner()
        self.message_id = message_id or uuid4().hex
        failure = None
        size = 0
        try:
            if self.settings is None:
                raise DingTalkSendError(
                    "AI card streaming requires a card template"
                )
            target = self._target(event)
            async for chunk in source:
                if not isinstance(chunk, str):
                    raise TypeError("Stream increments must be text")
                size += len(chunk.encode("utf-8"))
                if (
                    size > MAX_STREAM_BYTES
                    or len(self.content) + len(chunk)
                    > self.channel.settings.max_message_length
                ):
                    raise ValueError("Card stream exceeds the size limit")
                self.content += chunk
                if not chunk or not self.content.strip():
                    continue
                if self.card_id is None:
                    await self._create(event, target)
                elif (
                    anyio.current_time() - self._last_update
                    >= self.settings.update_interval
                ):
                    await self._update()
            if self.card_id is None:
                raise ValueError("Stream answers require readable content")
        except BaseException as error:
            failure = error
        finally:
            try:
                await close_source(source)
            except BaseException as error:
                if failure is None:
                    failure = error
                else:
                    failure.add_note(
                        f"Stream cleanup failed: {type(error).__name__}"
                    )
        if failure is None:
            try:
                await self._update(AICardStatus.FINISHED)
            except BaseException as error:
                failure = error
        if failure is not None:
            if self.card_id is not None:
                try:
                    with anyio.fail_after(
                        self.channel.protocol.request_timeout, shield=True
                    ):
                        await self._update(AICardStatus.FAILED)
                except BaseException as error:
                    failure.add_note(
                        "Card failure notification failed: "
                        f"{type(error).__name__}"
                    )
            raise failure
        return {"outTrackId": self.card_id}

    def _target(self, event: DingTalkMessageEvent) -> dict:
        if event.conversation_type == "2" and event.conversation_id:
            return {
                "openSpaceId": f"dtv1.card//IM_GROUP.{event.conversation_id}",
                "imGroupOpenDeliverModel": {
                    "robotCode": self.channel.settings.client_id
                },
            }
        staff_id = event.raw.get("senderStaffId")
        if (
            event.conversation_type != "1"
            or not isinstance(staff_id, str)
            or not staff_id
        ):
            raise DingTalkSendError("Private AI cards require a staff user ID")
        return {
            "openSpaceId": f"dtv1.card//IM_ROBOT.{staff_id}",
            "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT"},
        }

    async def _create(self, event: DingTalkMessageEvent, target: dict) -> None:
        self.token = await self.client.access_token(self.channel.settings)
        self.card_id = CardReplier.gen_card_id(
            ChatbotMessage.from_dict(event.raw)
        )
        await self._request(
            "card_create",
            {
                "cardTemplateId": self.settings.template_id,
                "outTrackId": self.card_id,
                "cardData": {
                    "cardParamMap": {
                        self.settings.content_key: self.content,
                        "flowStatus": AICardStatus.INPUTING.value,
                    }
                },
                "callbackType": "STREAM",
                "imGroupOpenSpaceModel": {"supportForward": False},
                "imRobotOpenSpaceModel": {"supportForward": False},
                **target,
            },
        )
        self._last_update = anyio.current_time()

    async def _update(
        self, status: AICardStatus = AICardStatus.INPUTING
    ) -> None:
        await anyio.sleep(
            max(
                0,
                self.settings.update_interval
                - (anyio.current_time() - self._last_update),
            )
        )
        if not self._finalized:
            await self._request(
                "card_update",
                {
                    "outTrackId": self.card_id,
                    "guid": uuid4().hex,
                    "key": self.settings.content_key,
                    "content": self.content,
                    "isFull": True,
                    "isFinalize": status != AICardStatus.INPUTING,
                    "isError": status == AICardStatus.FAILED,
                },
            )
            self._finalized = status != AICardStatus.INPUTING
            self._last_update = anyio.current_time()
        if status != AICardStatus.INPUTING:
            await self._request(
                "card_end",
                {
                    "outTrackId": self.card_id,
                    "cardData": {
                        "cardParamMap": {
                            self.settings.content_key: self.content,
                            "flowStatus": status.value,
                        }
                    },
                    "cardUpdateOptions": {"updateCardDataByKey": True},
                },
            )

    async def _request(self, kind: str, body: dict) -> None:
        async with self.delivery.operation(
            self.message_id,
            kind=kind,
            offset=len(self.content.encode("utf-8")),
            original_type="markdown",
            actual_type="card",
        ) as operation:
            if kind == "card_create":
                pending = (
                    self.client.cards.create_and_deliver_with_options_async(
                        models.CreateAndDeliverRequest().from_map(body),
                        models.CreateAndDeliverHeaders(
                            x_acs_dingtalk_access_token=self.token
                        ),
                        self.client.options,
                    )
                )
            elif kind == "card_update":
                pending = self.client.cards.streaming_update_with_options_async(
                    models.StreamingUpdateRequest().from_map(body),
                    models.StreamingUpdateHeaders(
                        x_acs_dingtalk_access_token=self.token
                    ),
                    self.client.options,
                )
            else:
                pending = self.client.cards.update_card_with_options_async(
                    models.UpdateCardRequest().from_map(body),
                    models.UpdateCardHeaders(
                        x_acs_dingtalk_access_token=self.token
                    ),
                    self.client.options,
                )
            result = await self.client.invoke(pending)
            if _rejected(result):
                raise DingTalkSendError("DingTalk rejected the card operation")
            if kind == "card_create" and (
                not isinstance(result, dict) or not result
            ):
                raise DingTalkSendError(
                    "DingTalk did not confirm card creation"
                )
            operation.response = result


def _rejected(value: object) -> bool:
    """Catch business failures nested inside an HTTP 200 delivery response."""
    if isinstance(value, dict):
        return (
            value.get("success") is False
            or value.get("result") is False
            or any(_rejected(item) for item in value.values())
        )
    return isinstance(value, list) and any(_rejected(item) for item in value)
