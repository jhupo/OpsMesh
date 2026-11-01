"""Participant continuity and isolation across batches and proactive input."""

import json
from dataclasses import replace
from datetime import UTC, datetime

from opsmesh.application.conversations.transcript import conversation_input
from opsmesh.modules.bots.conversations import QueuedConversationMessage


def test_speakers_remain_stable_across_reordering_and_conversation_modes():
    message = QueuedConversationMessage(
        event_id="first",
        bot_id="bot",
        channel_id="group-a",
        user_id="private-openid-a",
        message_id="provider-first",
        text="How is the project going?",
        occurred_at=datetime.now(UTC),
        to_me=False,
    )
    other = replace(message, user_id="private-openid-b", event_id="second")

    def transcript(messages, purpose="chat"):
        return json.loads(conversation_input(messages, purpose=purpose, reply_character_limit=700))[
            "messages"
        ]

    first = transcript([message, other])
    later = transcript([other, message], "activity")
    assert first[0]["sender"] == later[1]["sender"]
    assert first[1]["sender"] == later[0]["sender"] != first[0]["sender"]
    assert transcript([replace(message, channel_id="group-b")])[0]["sender"] != first[0]["sender"]
    assert transcript([replace(message, bot_id="another-bot")])[0]["sender"] != first[0]["sender"]
    assert "private-openid" not in json.dumps(first)
    assert first[0]["occurred_at"] == message.occurred_at.isoformat()
    assert first[0]["mentioned"] is False
