"""Map database constraint failures to neutral conflicts."""


class DatabaseConflictError(Exception):
    """A unique or foreign-key constraint rejected the staged write."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
