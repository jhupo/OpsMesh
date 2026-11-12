"""QQ channel error types with client-safe messages."""


class QQError(Exception):
    """Base error for the QQ channel."""


class QQConfigurationError(QQError, ValueError):
    """A connection or protocol setting is unusable."""


class QQSignatureError(QQError):
    """A callback signature or timestamp failed verification."""


class QQProtocolError(QQError):
    """A verified callback body is not a supported QQ payload."""


class QQSendError(QQError):
    """A QQ vendor request did not produce a usable result."""
