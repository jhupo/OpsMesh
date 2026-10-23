"""Convert only the current ingress batch to native multimodal input values."""

import base64
from typing import Any

from opsmesh.gateway.contracts import GatewayTurn
from opsmesh.gateway.messaging.media import GatewayMedia
from opsmesh.shared.serialization import canonical_json


async def native_messages(turn: GatewayTurn, media: GatewayMedia) -> list[dict[str, Any]]:
    messages = []
    for message in [item.message for item in turn.batch] or [turn.message]:
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": canonical_json(
                    {
                        "event_id": message.event_id,
                        "sender": message.actor.model_dump(mode="json"),
                        "conversation": message.conversation.model_dump(mode="json"),
                        "context": message.context.model_dump(
                            mode="json", exclude={"recent_messages"}
                        ),
                        "text": [part.text for part in message.content if part.type == "text"],
                    }
                ),
            }
        ]
        for part in await media.prepare([part for part in message.content if part.type != "text"]):
            if part.media_key:
                data = base64.b64encode(await media.read(part.media_key)).decode()
                url = f"data:{part.mime_type};base64,{data}"
                content.append(
                    {"type": "input_image", "image_url": url}
                    if part.type == "image"
                    else {"type": "input_file", "filename": part.filename, "file_data": url}
                )
        messages.append({"role": "user", "content": content})
    return messages
