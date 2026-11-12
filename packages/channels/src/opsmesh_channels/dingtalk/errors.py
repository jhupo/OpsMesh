"""DingTalk channel error types with client-safe messages."""


class DingTalkError(Exception):
    """Base error for the DingTalk channel."""


class DingTalkConfigurationError(DingTalkError, ValueError):
    """A connection or protocol setting is unusable."""


class DingTalkSignatureError(DingTalkError):
    """A callback signature or timestamp failed verification."""


class DingTalkProtocolError(DingTalkError):
    """A verified callback body is not a supported DingTalk payload."""


class DingTalkSendError(DingTalkError):
    """A DingTalk reply could not be delivered."""
