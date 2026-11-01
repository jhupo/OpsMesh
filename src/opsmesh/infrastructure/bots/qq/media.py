"""QQ rich-media upload plans; uploading never sends a message implicitly."""

from urllib.parse import urlsplit

from opsmesh.framework.adapters import OpsMeshMessage
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import JsonData

_MEDIA_TYPES = {"image": 1, "video": 2, "audio": 3}


def media_upload(message: OpsMeshMessage) -> JsonData | None:
    media = [segment for segment in message if segment.type in _MEDIA_TYPES]
    if not media:
        return None
    if len(media) != 1 or any(segment.type not in {*_MEDIA_TYPES, "text"} for segment in message):
        raise OpsMeshError("UNSUPPORTED_FEATURE", "QQ supports one media item with optional text")
    resource = media[0].data.get("resource_ref")
    try:
        url = urlsplit(resource) if isinstance(resource, str) else None
        valid = (
            url is not None
            and url.scheme == "https"
            and bool(url.hostname)
            and url.username is None
            and url.password is None
            and url.port in {None, 443}
            and not url.fragment
            and isinstance(resource, str)
            and len(resource) <= 8192
        )
    except ValueError:
        valid = False
    if not valid:
        raise OpsMeshError("INVALID_REQUEST", "QQ media requires an HTTPS resource URL")
    return {"file_type": _MEDIA_TYPES[media[0].type], "url": resource, "srv_send_msg": False}
