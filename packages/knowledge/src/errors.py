"""Errors safe to expose through knowledge objects and native tools."""


class KnowledgeError(RuntimeError):
    """A knowledge operation could not complete."""


class KnowledgeClosed(KnowledgeError):
    """Knowledge resources are stopping or unavailable."""


class KnowledgeNotFound(KnowledgeError):
    """The resource does not exist in the selected ownership scope."""


class InvalidDocument(KnowledgeError, ValueError):
    """The source is empty, unsupported, or outside configured bounds."""


class InvalidQuery(KnowledgeError, ValueError):
    """The query cannot be encoded without losing input."""


class IndexUnavailable(KnowledgeError):
    """The library needs rebuilding or has unfinished indexing."""


class StaleReference(KnowledgeError):
    """The cited document index has been replaced."""


class ModelConfigurationError(KnowledgeError):
    """The local model does not satisfy the pinned encoder contract."""


class EncodingBusy(KnowledgeError):
    """The bounded encoder has no admission capacity."""
