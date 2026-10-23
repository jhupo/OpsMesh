"""Shared target execution ports for schedules and evaluation experiments."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.users.access import CurrentUser


@dataclass(frozen=True)
class TargetPorts:
    authorize: Callable[[Session, CurrentUser, str, UUID], None]
    submit: Callable[[Session, CurrentUser, str, UUID, object, str], UUID]
    result: Callable[[Session, CurrentUser, str, UUID], dict[str, object]]
    cancel: Callable[[Session, CurrentUser, str, UUID], None]
