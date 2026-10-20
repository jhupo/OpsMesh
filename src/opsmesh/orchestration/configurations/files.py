"""Shared directory boundaries for configuration APIs and delegated host operations."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.configurations.models import ConfigurationMember, UseConfiguration
from opsmesh.orchestration.configurations.service import authorize_configuration
from opsmesh.resources.files.access import ResourceReader
from opsmesh.resources.files.models import ResourceDirectory, ResourceFile
from opsmesh.resources.files.service import get_file
from opsmesh.shared.errors import ForbiddenError, NotFoundError


def authorize_configuration_directory(
    session: Session,
    *,
    actor: CurrentUser,
    configuration_id: UUID,
    directory_id: UUID,
    write: bool = False,
) -> tuple[UseConfiguration, ResourceDirectory]:
    configuration = authorize_configuration(
        session,
        actor=actor,
        configuration_id=configuration_id,
        permission="use" if write else "read",
    )
    if actor.user_id != configuration.owner_user_id:
        member = session.scalar(
            select(ConfigurationMember).where(
                ConfigurationMember.configuration_id == configuration_id,
                ConfigurationMember.user_id == actor.user_id,
            )
        )
        if member is None or not member.directory_access:
            raise ForbiddenError("The configuration invitation does not grant directory access")
    directory = session.get(ResourceDirectory, directory_id)
    if directory is None or directory.owner_user_id != configuration.owner_user_id:
        raise NotFoundError("Shared directory not found")
    current: ResourceDirectory | None = directory
    seen: set[UUID] = set()
    while current is not None and current.id not in seen:
        seen.add(current.id)
        if current.owner_user_id != configuration.owner_user_id:
            break
        if current.id == configuration.root_directory_id:
            return configuration, directory
        current = session.get(ResourceDirectory, current.parent_id) if current.parent_id else None
    raise ForbiddenError("The directory is outside the selected working root")


def authorize_configuration_file(
    session: Session,
    *,
    actor: CurrentUser,
    configuration_id: UUID,
    file_id: UUID,
    write: bool = False,
) -> tuple[UseConfiguration, ResourceFile]:
    configuration = authorize_configuration(
        session,
        actor=actor,
        configuration_id=configuration_id,
        permission="use" if write else "read",
    )
    resource, _ = get_file(
        session, file_id=file_id, reader=ResourceReader(configuration.owner_user_id)
    )
    authorize_configuration_directory(
        session,
        actor=actor,
        configuration_id=configuration_id,
        directory_id=resource.directory_id,
        write=write,
    )
    return configuration, resource
