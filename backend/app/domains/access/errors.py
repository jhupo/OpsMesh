from dataclasses import dataclass


@dataclass(frozen=True)
class AuthorizationError(Exception):
    message: str


class AuthenticationError(AuthorizationError):
    pass


class PermissionDeniedError(AuthorizationError):
    pass

