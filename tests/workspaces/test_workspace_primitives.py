"""S6 workspace primitive tests: role contracts, invitation states and schemas.

These tests need no database: they cover the pure contracts the workspace
step stores and derives — the space and project role sets, the derived
invitation expiry, and the request models that reject unknown fields.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from opsmesh.shared.errors import InvalidArgumentError
from opsmesh.workspaces.credentials.schemas import CredentialCreateRequest, validated_kind
from opsmesh.workspaces.members.models import (
    INVITATION_EXPIRED,
    INVITATION_PENDING,
    INVITATION_REVOKED,
    SPACE_INVITATION_ROLES,
    SPACE_ROLES,
    WorkspaceInvitation,
)
from opsmesh.workspaces.members.schemas import InvitationCreateRequest, MemberRoleUpdateRequest
from opsmesh.workspaces.model_providers.models import (
    MODEL_PROVIDERS,
    PROBE_STATUSES,
    PROVIDER_OPENAI,
)
from opsmesh.workspaces.model_providers.schemas import ModelProviderCreateRequest
from opsmesh.workspaces.notifications.models import NOTIFICATION_SEVERITIES
from opsmesh.workspaces.notifications.schemas import NotificationCreateRequest
from opsmesh.workspaces.projects.members.models import (
    PROJECT_ROLES,
    ProjectInvitation,
)
from opsmesh.workspaces.projects.members.schemas import validated_role
from opsmesh.workspaces.projects.schemas import ProjectCreateRequest
from opsmesh.workspaces.schemas import CapacityDefaults, WorkspaceCreateRequest


def invitation(**overrides) -> WorkspaceInvitation:
    values = {
        "workspace_id": uuid4(),
        "invitee_id": uuid4(),
        "invited_by_user_id": uuid4(),
        "role": "member",
        "state": INVITATION_PENDING,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    values.update(overrides)
    return WorkspaceInvitation(**values)


class TestInvitationStates:
    def test_pending_invitation_stays_pending_before_expiry(self) -> None:
        assert invitation().effective_state() == INVITATION_PENDING

    def test_expiry_is_derived_and_never_stored(self) -> None:
        aged = invitation(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        assert aged.state == INVITATION_PENDING
        assert aged.effective_state() == INVITATION_EXPIRED

    def test_decided_states_are_returned_as_stored(self) -> None:
        assert invitation(state=INVITATION_REVOKED).effective_state() == INVITATION_REVOKED

    def test_project_invitation_derives_expiry_the_same_way(self) -> None:
        aged = ProjectInvitation(
            workspace_id=uuid4(),
            project_id=uuid4(),
            invitee_id=uuid4(),
            invited_by_user_id=uuid4(),
            role="viewer",
            state=INVITATION_PENDING,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        assert aged.effective_state() == INVITATION_EXPIRED


class TestRoleContracts:
    def test_space_roles_contain_exactly_one_owner_role(self) -> None:
        assert SPACE_ROLES == ("owner", "admin", "member", "viewer")

    def test_invitation_roles_never_include_owner(self) -> None:
        assert SPACE_INVITATION_ROLES == ("admin", "member", "viewer")

    def test_project_roles_have_no_owner(self) -> None:
        assert PROJECT_ROLES == ("admin", "member", "viewer")
        with pytest.raises(ValueError):
            validated_role("owner")


class TestRequestContracts:
    def test_workspace_create_refuses_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            WorkspaceCreateRequest.model_validate({"name": "x", "unexpected": 1})

    def test_member_role_update_refuses_owner_role(self) -> None:
        with pytest.raises(ValidationError):
            MemberRoleUpdateRequest.model_validate({"role": "owner"})

    def test_invitation_ttl_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            InvitationCreateRequest.model_validate(
                {"invitee_user_id": str(uuid4()), "role": "member", "ttl_seconds": 60}
            )

    def test_project_create_refuses_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            ProjectCreateRequest.model_validate({"name": "x", "extra": True})

    def test_credential_secret_payload_is_bounded(self) -> None:
        with pytest.raises(ValidationError):
            CredentialCreateRequest.model_validate(
                {"name": "k", "kind": "capability", "secret": {}}
            )
        with pytest.raises(ValidationError):
            CredentialCreateRequest.model_validate(
                {"name": "k", "kind": "capability", "secret": {"a": "x" * 5000}}
            )

    def test_unknown_credential_kind_is_a_request_argument_error(self) -> None:
        """A stated kind the platform does not define must reach the HTTP
        boundary as the domain error it maps to 400, not a raw ValueError."""
        with pytest.raises(InvalidArgumentError):
            validated_kind("nonsense")
        assert validated_kind("capability") == "capability"

    def test_model_provider_default_model_must_be_declared(self) -> None:
        with pytest.raises(ValidationError):
            ModelProviderCreateRequest.model_validate(
                {
                    "name": "openai",
                    "credential_id": str(uuid4()),
                    "default_model": "gpt-5",
                    "models": ["gpt-4"],
                }
            )

    def test_notification_severity_is_enumerated(self) -> None:
        assert NOTIFICATION_SEVERITIES == ("info", "warning", "critical")
        with pytest.raises(ValidationError):
            NotificationCreateRequest.model_validate(
                {
                    "recipient_user_id": str(uuid4()),
                    "notification_type": "x",
                    "source_type": "y",
                    "title": "t",
                    "severity": "fatal",
                }
            )


class TestProviderContracts:
    def test_only_the_openai_provider_is_supported(self) -> None:
        assert MODEL_PROVIDERS == (PROVIDER_OPENAI,)

    def test_probe_statuses_keep_unknown_distinct_from_failure(self) -> None:
        assert PROBE_STATUSES == ("never", "running", "ok", "failed", "needs_attention")

    def test_capacity_defaults_render_both_kinds(self) -> None:
        limits = CapacityDefaults(concurrency_limit=2, environment_limit=None).as_limits()
        assert limits == {"concurrency": 2, "environment": None}
