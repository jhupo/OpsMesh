"""Configuration for Tencent's official QQ botpy client."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Intents(BaseModel):
    """The event flags accepted by ``botpy.flags.Intents``."""

    model_config = ConfigDict(extra="forbid")

    guilds: bool = False
    guild_members: bool = False
    guild_messages: bool = False
    guild_message_reactions: bool = False
    direct_message: bool = True
    interaction: bool = False
    message_audit: bool = False
    forums: bool = False
    audio_action: bool = False
    public_guild_messages: bool = True
    public_messages: bool = True
    open_forum_event: bool = False
    audio_or_live_channel_member: bool = False

    def to_botpy(self) -> Any:
        """Build the official SDK flag object without duplicating its bit layout."""
        from botpy.flags import Intents as BotpyIntents

        return BotpyIntents(**self.model_dump())


class QQConfig(BaseModel):
    """Credentials and event subscriptions for one QQ bot connection."""

    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(min_length=1, max_length=100)
    secret: SecretStr = Field(min_length=1)
    intents: Intents = Field(default_factory=Intents)
