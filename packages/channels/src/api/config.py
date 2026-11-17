"""HTTP transport settings supplied by the consuming plugin."""

from pydantic import ConfigDict, Field

from ..attachments import AttachmentSettings
from .server import ListenerSettings


class Settings(ListenerSettings):
    """Bound one consumer-owned HTTP listener and its message transfers."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    attachments: AttachmentSettings = Field(default_factory=AttachmentSettings)
    request_timeout_seconds: float = Field(default=300, gt=0)
    max_output_messages: int = Field(default=128, gt=0)
    max_output_bytes: int = Field(default=8 * 1024 * 1024, gt=0)
    stream_buffer_size: int = Field(default=16, gt=0, le=1024)
