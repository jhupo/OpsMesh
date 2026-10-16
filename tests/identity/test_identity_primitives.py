"""Identity primitive tests: password shape, invitation tokens, derived status and body."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from opsmesh.identity.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    validate_password_shape,
    verify_password,
)
from opsmesh.identity.errors import InvalidCredentialsError
from opsmesh.identity.invitations import service as invitations
from opsmesh.identity.invitations.models import (
    INVITATION_ACCEPTED,
    INVITATION_EXPIRED,
    INVITATION_PENDING,
    INVITATION_REVOKED,
    AccountInvitation,
)


def test_a_password_inside_the_bounds_is_accepted() -> None:
    validate_password_shape("x" * MIN_PASSWORD_LENGTH)
    validate_password_shape("x" * MAX_PASSWORD_LENGTH)


@pytest.mark.parametrize("password", ["", "x" * (MIN_PASSWORD_LENGTH - 1), "x" * 257])
def test_an_unusable_password_is_rejected(password: str) -> None:
    with pytest.raises(ValueError):
        validate_password_shape(password)


def test_a_hashed_password_verifies_and_hides_the_secret() -> None:
    stored = hash_password("correct-horse-battery")
    assert stored != "correct-horse-battery"
    assert verify_password("correct-horse-battery", stored)
    assert not verify_password("wrong-horse-battery", stored)


def test_a_malformed_stored_hash_fails_closed() -> None:
    assert not verify_password("correct-horse-battery", "not-a-hash")


def test_the_invitation_token_is_only_stored_as_a_digest() -> None:
    token = uuid4()
    digest = invitations.token_digest(token)
    assert digest != str(token)
    assert len(digest) == 64
    assert invitations.token_digest(token) == digest
    assert invitations.token_digest(uuid4()) != digest


def invitation(status: str, *, expires_at: datetime) -> AccountInvitation:
    return AccountInvitation(
        email="invitee@example.com",
        token_hash="0" * 64,
        status=status,
        expires_at=expires_at,
    )


def test_a_pending_invitation_inside_its_lifetime_is_pending() -> None:
    now = datetime(2026, 10, 16, tzinfo=UTC)
    row = invitation(INVITATION_PENDING, expires_at=now + timedelta(hours=1))
    assert invitations.effective_status(row, now=now) == INVITATION_PENDING


def test_a_pending_invitation_past_its_lifetime_presents_as_expired() -> None:
    now = datetime(2026, 10, 16, tzinfo=UTC)
    row = invitation(INVITATION_PENDING, expires_at=now - timedelta(seconds=1))
    assert invitations.effective_status(row, now=now) == INVITATION_EXPIRED


@pytest.mark.parametrize("status", [INVITATION_ACCEPTED, INVITATION_REVOKED])
def test_a_closed_invitation_keeps_its_stored_status(status: str) -> None:
    now = datetime(2026, 10, 16, tzinfo=UTC)
    row = invitation(status, expires_at=now - timedelta(days=1))
    assert invitations.effective_status(row, now=now) == status


def test_the_invitation_body_carries_the_token_and_expiry() -> None:
    token = uuid4()
    expires_at = datetime(2026, 10, 23, tzinfo=UTC)
    body = invitations.render_invitation_body(
        token=token, expires_at=expires_at, base_url="https://opsmesh.example.com/"
    )
    assert str(token) in body
    assert expires_at.isoformat() in body
    assert f"https://opsmesh.example.com/accept-invitation?token={token}" in body


def test_the_invitation_body_works_without_a_base_url() -> None:
    token = uuid4()
    body = invitations.render_invitation_body(
        token=token, expires_at=datetime(2026, 10, 23, tzinfo=UTC), base_url=None
    )
    assert str(token) in body
    assert "accept-invitation" not in body


def test_the_default_lifetime_is_inside_the_accepted_range() -> None:
    assert (
        invitations.MIN_INVITATION_TTL_SECONDS
        <= invitations.DEFAULT_INVITATION_TTL_SECONDS
        <= invitations.MAX_INVITATION_TTL_SECONDS
    )


def test_invalid_credentials_carry_the_stable_code() -> None:
    error = InvalidCredentialsError()
    assert error.code == "invalid_credentials"
    assert error.status_code == 401
