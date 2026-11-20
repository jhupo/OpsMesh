"""Event-scoped attachment metadata and transfer interfaces."""

from .models import Attachment, AttachmentError, AttachmentSettings
from .transfer import AttachmentCollection

__all__ = [
    "Attachment",
    "AttachmentError",
    "AttachmentSettings",
    "AttachmentCollection",
]
