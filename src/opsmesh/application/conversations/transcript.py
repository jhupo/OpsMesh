"""Consistent, channel-scoped participant identities for conversational model input."""

import hashlib
import json
from collections.abc import Sequence
from typing import Literal

from opsmesh.modules.bots.conversations import QueuedConversationMessage


def conversation_input(
    messages: Sequence[QueuedConversationMessage],
    *,
    purpose: Literal["chat", "activity"],
    reply_character_limit: int,
) -> str:
    transcript = []
    for message in messages:
        identity = json.dumps(
            [message.bot_id, message.channel_id, message.user_id], separators=(",", ":")
        )
        speaker = "member-" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        transcript.append(
            {
                "event_id": message.event_id,
                "sender": speaker,
                "text": message.text,
                "mentioned": message.to_me,
                "occurred_at": message.occurred_at.isoformat(),
            }
        )
    return json.dumps(
        {
            "purpose": purpose,
            "reply_character_limit": reply_character_limit,
            "messages": transcript,
        },
        ensure_ascii=False,
    )
