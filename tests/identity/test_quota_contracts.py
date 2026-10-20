"""Quantity-only quota and capacity contracts."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from opsmesh.identity.quotas.schemas import NormalizedUsage, QuotaLimitsRequest
from opsmesh.identity.quotas.service import ensure_capacity_accounts
from opsmesh.shared.errors import InvalidArgumentError


def test_capacity_initialization_requires_all_kinds_explicitly():
    with pytest.raises(InvalidArgumentError, match="Every capacity kind"):
        ensure_capacity_accounts(None, user_id=uuid4(), limits={"concurrency": 1})


def test_usage_contains_only_nonnegative_quantities():
    with pytest.raises(ValidationError):
        NormalizedUsage(input_tokens=-1)
    with pytest.raises(ValidationError):
        NormalizedUsage.model_validate({"currency": "USD"})
    assert NormalizedUsage(input_tokens=3, cached_input_tokens=2).model_dump() == {
        "input_tokens": 3,
        "output_tokens": 0,
        "cached_input_tokens": 2,
        "tool_invocations": 0,
    }


def test_quota_limits_do_not_accept_monetary_budget():
    with pytest.raises(ValidationError):
        QuotaLimitsRequest.model_validate({"budget": 100})
