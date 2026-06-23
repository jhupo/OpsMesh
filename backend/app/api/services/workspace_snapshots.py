from __future__ import annotations

from datetime import UTC, datetime

from backend.app.workspaces.models import WorkspaceInvite, WorkspaceMember, WorkspaceQuota


def quota_snapshot(quota: WorkspaceQuota) -> dict[str, object]:
    return {
        "quota_key": quota.quota_key,
        "limit_value": quota.limit_value,
        "reserved_value": quota.reserved_value,
        "unit": quota.unit,
        "status": quota.status,
    }


def member_snapshot(member: WorkspaceMember) -> dict[str, object]:
    return {
        "id": str(member.id),
        "workspace_id": str(member.workspace_id),
        "user_id": str(member.user_id),
        "role": member.role,
        "status": member.status,
    }


def invite_snapshot(invite: WorkspaceInvite) -> dict[str, object]:
    return {
        "id": str(invite.id),
        "workspace_id": str(invite.workspace_id),
        "email": invite.email,
        "role": invite.role,
        "status": invite.status,
        "fingerprint": invite.fingerprint,
        "invitee_user_id": str(invite.invitee_user_id) if invite.invitee_user_id else None,
        "accepted_by_user_id": (
            str(invite.accepted_by_user_id) if invite.accepted_by_user_id else None
        ),
        "revoked_by_user_id": (
            str(invite.revoked_by_user_id) if invite.revoked_by_user_id else None
        ),
        "expires_at": canonical_datetime(invite.expires_at),
        "accepted_at": canonical_datetime(invite.accepted_at),
        "revoked_at": canonical_datetime(invite.revoked_at),
    }


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def canonical_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return as_utc(value).replace(tzinfo=None).isoformat()
