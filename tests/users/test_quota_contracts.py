"""Removed disk quotas cannot be mistaken for an enforced API guarantee."""

import pytest
from pydantic import ValidationError

from opsmesh.bootstrap.models import register_models
from opsmesh.users.quotas.schemas import QuotaLimitsRequest, QuotaView
from opsmesh.users.schemas import AdminCreateUserRequest


def test_removed_storage_limit_is_rejected_by_account_and_quota_requests():
    with pytest.raises(ValidationError) as error:
        QuotaLimitsRequest.model_validate({"storage_limit_bytes": 1024})
    assert error.value.errors()[0]["loc"] == ("storage_limit_bytes",)
    payload = {
        "email": "quota@example.com",
        "display_name": "Quota",
        "password": "valid-password-123",
    }
    AdminCreateUserRequest.model_validate(payload)
    with pytest.raises(ValidationError) as error:
        AdminCreateUserRequest.model_validate({**payload, "storage_limit_bytes": 1024})
    assert error.value.errors()[0]["loc"] == ("storage_limit_bytes",)


def test_quota_response_only_contains_token_allowance_and_usage():
    assert set(QuotaView.model_fields) == {
        "token_limit",
        "used_tokens",
        "reserved_tokens",
        "available_tokens",
    }


def test_current_schema_does_not_create_a_storage_quota_ledger():
    metadata = register_models()
    assert "storage_reservations" not in metadata.tables
    assert "user_storage_accounts" not in metadata.tables
    assert "storage_limit_bytes" not in metadata.tables["user_quota_limits"].c
