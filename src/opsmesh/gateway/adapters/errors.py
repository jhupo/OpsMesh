"""Safe gateway rejection codes within the host's domain error hierarchy."""

from opsmesh.shared.errors import DomainError


class OperationRejected(DomainError):
    """A definite rejection; ambiguous transport failures must not use this error."""

    def __init__(self, code: str) -> None:
        super().__init__(code, code=code)
