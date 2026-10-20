"""Resolve native sandbox mounts from current environment and resource authority."""

from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.bootstrap.orchestration import (
    ConfigurationCleanupAuthorization,
    ConfigurationDirectories,
)
from opsmesh.identity.users.service import load_identity
from opsmesh.orchestration.configurations.files import authorize_configuration_directory
from opsmesh.orchestration.configurations.models import ConfigurationClosure, UseConfiguration
from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject, ScopeAdmission
from opsmesh.orchestration.runtime.contracts import SandboxDirectory
from opsmesh.orchestration.runtime.models import Environment, EnvironmentOperationReceipt
from opsmesh.resources.files.models import ResourceDirectory
from opsmesh.shared.errors import ForbiddenError
from opsmesh.shared.files import resolve_within_root


class SandboxDirectories:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        storage_root: Path,
        admission: ScopeAdmission,
    ) -> None:
        self.sessions, self.storage_root, self.admission = sessions, storage_root, admission

    def __call__(
        self,
        *,
        environment_id: UUID,
        principal_id: UUID,
        directory_id: UUID | None,
    ) -> SandboxDirectory:
        with self.sessions() as session, session.begin():
            environment = session.get(Environment, environment_id, with_for_update=True)
            actor = load_identity(session, principal_id)
            if environment is None or environment.principal_id != principal_id or actor is None:
                raise ForbiddenError("The environment directory owner is unavailable")
            configuration_id = (
                environment.scope_id if environment.owner_ref.startswith("configuration:") else None
            )
            scope = ExecutionScope(configuration_id=configuration_id)
            stored = environment.configuration.get("root_directory_id")
            if stored is not None and directory_id is not None and str(directory_id) != stored:
                raise ForbiddenError("The environment directory changed")
            closing = (
                session.get(
                    EnvironmentOperationReceipt,
                    (environment.scope_id, environment.active_operation_id),
                )
                if environment.active_operation_id
                else None
            )
            cleanup = bool(closing and closing.action == "close" and configuration_id)
            if cleanup:
                closure = session.scalar(
                    select(ConfigurationClosure).where(
                        ConfigurationClosure.configuration_id == configuration_id,
                    )
                )
                if closure is None:
                    raise ForbiddenError("The environment has no recorded cleanup decision")
                ConfigurationCleanupAuthorization().admit_cleanup(
                    session,
                    cleanup_id=closure.id,
                    subject=ExecutionSubject(principal_id),
                    scope=scope,
                )
            else:
                self.admission.admit(session, subject=ExecutionSubject(principal_id), scope=scope)
            chosen = UUID(str(stored)) if stored else directory_id
            if configuration_id and session.get(UseConfiguration, configuration_id) is None:
                raise ForbiddenError("The configuration is unavailable")
            if chosen is None:
                chosen = ConfigurationDirectories().ensure_directory(
                    session,
                    owner_user_id=principal_id,
                    configuration_id=configuration_id or environment.id,
                    directory_id=None,
                )
            directory = session.get(ResourceDirectory, chosen)
            if directory is None:
                raise ForbiddenError("The sandbox directory is unavailable to its owner")
            if directory.owner_user_id != principal_id:
                if configuration_id is None or (cleanup and stored is None):
                    raise ForbiddenError("The sandbox directory belongs to another user")
                if not cleanup:
                    authorize_configuration_directory(
                        session,
                        actor=actor,
                        configuration_id=configuration_id,
                        directory_id=directory.id,
                        write=True,
                    )
            environment.configuration = {
                **environment.configuration,
                "root_directory_id": str(directory.id),
            }
            storage_root = self.storage_root.resolve()
            owner_relative = f"workdirs/{directory.owner_user_id}"
            candidate = storage_root
            for component in (owner_relative + "/" + directory.path).split("/"):
                candidate = candidate / component
                if candidate.is_symlink():
                    raise ForbiddenError("Sandbox directory components cannot be symbolic links")
            owner_root = resolve_within_root(storage_root, owner_relative)
            host_path = resolve_within_root(owner_root, directory.path)
            host_path.mkdir(parents=True, exist_ok=True)
            return SandboxDirectory(directory.id, host_path)
