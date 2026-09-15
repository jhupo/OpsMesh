from __future__ import annotations

from uuid import UUID


class WorkspaceMemberNotFoundError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WorkspaceMemberConflictError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WorkspaceMemberPermissionError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WorkspaceInviteNotFoundError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class WorkspaceInviteConflictError(Exception):
    def __init__(
        self,
        message: str,
        *,
        workspace_id: UUID | None = None,
        fingerprint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.workspace_id = workspace_id
        self.fingerprint = fingerprint


class WorkspaceInvitePermissionError(Exception):
    def __init__(
        self,
        message: str,
        *,
        workspace_id: UUID | None = None,
        fingerprint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.workspace_id = workspace_id
        self.fingerprint = fingerprint
