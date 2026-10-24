"""First-administrator CLI tests: decision table, parser and password intake."""

from __future__ import annotations

import pytest

from opsmesh.bootstrap.cli import (
    InitializationState,
    build_parser,
    decide_initialization,
    read_password,
    validated_email,
)


def state(**overrides: object) -> InitializationState:
    values: dict[str, object] = {
        "account_count": 0,
        "email_taken": False,
        "credential_present": False,
        "administrator": False,
    }
    values.update(overrides)
    return InitializationState(**values)  # type: ignore[arg-type]


def test_an_empty_platform_creates_the_administrator() -> None:
    assert decide_initialization(state()) == "create"


def test_the_administrator_this_command_created_reports_already_initialized() -> None:
    taken = state(account_count=1, email_taken=True, credential_present=True, administrator=True)
    assert decide_initialization(taken) == "already_initialized"


def test_a_populated_platform_refuses_a_new_administrator() -> None:
    assert decide_initialization(state(account_count=3)) == "conflict"


def test_an_existing_account_without_the_admin_role_is_a_conflict() -> None:
    taken = state(account_count=1, email_taken=True, credential_present=True)
    assert decide_initialization(taken) == "conflict"


def test_an_existing_admin_without_a_credential_is_a_conflict() -> None:
    taken = state(account_count=1, email_taken=True, administrator=True)
    assert decide_initialization(taken) == "conflict"


def test_the_parser_requires_the_administrator_inputs() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["init-admin", "--email", "admin@example.com", "--display-name", "Administrator"]
    )
    assert args.command == "init-admin"
    assert args.email == "admin@example.com"
    assert args.display_name == "Administrator"
    assert args.password_stdin is False
    with pytest.raises(SystemExit):
        parser.parse_args(["init-admin", "--email", "admin@example.com"])


def test_the_parser_accepts_the_stdin_password_channel() -> None:
    args = build_parser().parse_args(
        [
            "init-admin",
            "--email",
            "admin@example.com",
            "--display-name",
            "Administrator",
            "--password-stdin",
        ]
    )
    assert args.password_stdin is True


def test_the_email_is_validated_before_any_database_work() -> None:
    assert validated_email("admin@example.com") == "admin@example.com"
    with pytest.raises(ValueError):
        validated_email("not-an-email")


def test_the_stdin_password_is_read_once_and_must_not_be_empty(monkeypatch) -> None:
    import io as _io
    import sys

    monkeypatch.setattr(sys, "stdin", _io.StringIO("correct-horse\n"))
    assert read_password(from_stdin=True) == "correct-horse"
    monkeypatch.setattr(sys, "stdin", _io.StringIO("\n"))
    with pytest.raises(ValueError):
        read_password(from_stdin=True)
