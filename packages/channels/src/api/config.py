"""HTTP connection settings supplied by the consuming plugin."""

from pydantic import BaseModel, ConfigDict, Field

from ..attachments import AttachmentSettings


class APIConnectionSettings(BaseModel):
    """One consumer-owned HTTP listener and its message transfers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=0, le=65535)
    enabled: bool = True
    access_log: bool = False
    max_request_body: int = Field(default=1024 * 1024, ge=0)
    shutdown_timeout: float = Field(default=10.0, gt=0)
    attachments: AttachmentSettings = Field(default_factory=AttachmentSettings)
    request_timeout_seconds: float = Field(default=300, gt=0)
    max_output_messages: int = Field(default=128, gt=0)
    max_output_bytes: int = Field(default=8 * 1024 * 1024, gt=0)
    stream_buffer_size: int = Field(default=16, gt=0, le=1024)
