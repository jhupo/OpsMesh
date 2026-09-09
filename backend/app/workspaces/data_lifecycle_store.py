from sqlalchemy.orm import Session

from backend.app.workspaces.data_lifecycle_repository import WorkspaceDataLifecycleRepository


class LifecycleStore:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = WorkspaceDataLifecycleRepository(session)
