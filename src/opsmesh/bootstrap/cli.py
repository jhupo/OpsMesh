"""Platform command line: first administrator initialization and operations.

Process entry for the operations that run outside the API process. The S4
surface is the first administrator; the deploy host launcher delegates its
``bootstrap-admin`` command here, and later steps register their operations in
the same parser. The schema belongs to Alembic: apply the migrations before
running any command.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from opsmesh.bootstrap.resources import DatabaseResources, build_database_resources
from opsmesh.config import Settings, get_settings
from opsmesh.identity.auth.models import UserCredential
from opsmesh.identity.auth.passwords import validate_password_shape
from opsmesh.identity.provisioning import (
    PLATFORM_ADMINISTRATOR,
    AccountDefaults,
    provision_first_admin,
)
from opsmesh.identity.users import service as users
from opsmesh.identity.users.models import UserProfile
from opsmesh.shared.errors import DomainError

InitializationDecision = Literal["create", "already_initialized", "conflict"]


@dataclass(frozen=True)
class InitializationState:
    """What the database currently says about the requested administrator."""

    account_count: int
    email_taken: bool
    credential_present: bool
    administrator: bool


def decide_initialization(state: InitializationState) -> InitializationDecision:
    """Re-running the command is safe only for the very administrator it created."""
    if state.email_taken:
        if state.credential_present and state.administrator:
            return "already_initialized"
        return "conflict"
    if state.account_count:
        return "conflict"
    return "create"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opsmesh-cli", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_admin = subparsers.add_parser(
        "init-admin", help="Initialize the first platform administrator"
    )
    init_admin.add_argument("--email", required=True, help="Administrator email address")
    init_admin.add_argument("--display-name", required=True, help="Administrator display name")
    init_admin.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from the first line of standard input instead of prompting",
    )
    return parser


def read_password(*, from_stdin: bool) -> str:
    """Collect the password without ever echoing it or accepting it as an argument."""
    if from_stdin:
        line = sys.stdin.readline().rstrip("\r\n")
        if not line:
            raise ValueError("The password read from standard input is empty")
        return line
    password = getpass.getpass("Administrator password: ")
    if password != getpass.getpass("Repeat the password: "):
        raise ValueError("The two passwords do not match")
    return password


def validated_email(value: str) -> str:
    return str(TypeAdapter(EmailStr).validate_python(value))


def initialization_state(session: Session, *, email: str) -> InitializationState:
    account_count = session.scalar(select(func.count()).select_from(UserProfile)) or 0
    profile = users.find_by_email(session, email)
    if profile is None:
        return InitializationState(
            account_count=account_count,
            email_taken=False,
            credential_present=False,
            administrator=False,
        )
    credential = session.scalar(
        select(UserCredential).where(UserCredential.user_id == profile.id)
    )
    return InitializationState(
        account_count=account_count,
        email_taken=True,
        credential_present=credential is not None,
        administrator=PLATFORM_ADMINISTRATOR in profile.platform_roles,
    )


def initialize_first_administrator(
    settings: Settings, *, email: str, display_name: str, password: str
) -> int:
    """Create the first administrator, or report why the platform refuses one."""
    resources: DatabaseResources = build_database_resources(settings)
    try:
        with resources.sessions() as session:
            decision = decide_initialization(
                initialization_state(session, email=email)
            )
            if decision == "already_initialized":
                print(f"The first administrator already exists: {email}")
                return 0
            if decision == "conflict":
                print(
                    "The platform already has accounts; only the administrator this "
                    "command created may be re-initialized",
                    file=sys.stderr,
                )
                return 1
            defaults = AccountDefaults(
                token_limit=settings.default_user_token_limit,
                storage_limit_bytes=settings.default_user_storage_limit_bytes,
            )
            profile = provision_first_admin(
                session,
                email=email,
                display_name=display_name,
                password=password,
                defaults=defaults,
            )
            session.commit()
    except DomainError as exc:
        print(f"Initialization refused: {exc.message}", file=sys.stderr)
        return 1
    finally:
        resources.engine.dispose()
    print(f"Created the first administrator {profile.id} for {email}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "init-admin":
        parser.error(f"unsupported command: {args.command}")
    try:
        email = validated_email(args.email)
        if not args.display_name.strip():
            raise ValueError("The display name is empty")
        password = read_password(from_stdin=args.password_stdin)
        validate_password_shape(password)
    except ValueError as exc:
        print(f"Invalid input: {exc}", file=sys.stderr)
        return 2
    return initialize_first_administrator(
        get_settings(),
        email=email,
        display_name=args.display_name.strip(),
        password=password,
    )


if __name__ == "__main__":
    raise SystemExit(main())
