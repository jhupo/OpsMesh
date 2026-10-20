"""Audit append contract tests: credential screening, bounds and evidence references."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opsmesh.platform.audit.schemas import ExtensionAuditAppend


def append_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "action": "invocation.completed",
        "target_type": "invocation",
        "target_id": "target-1",
        "summary": "Invocation finished",
        "idempotency_key": "audit-key-0001",
    }
    payload.update(overrides)
    return payload


def test_a_minimal_append_is_accepted() -> None:
    append = ExtensionAuditAppend.model_validate(append_payload())
    assert append.details == {}
    assert append.evidence_refs == []


@pytest.mark.parametrize(
    "key",
    ["password", "Token", "api_key", "Authorization", "client_secret", "session_id"],
)
def test_credential_named_detail_fields_are_rejected(key: str) -> None:
    with pytest.raises(ValidationError):
        ExtensionAuditAppend.model_validate(append_payload(details={key: "value"}))


def test_nested_credential_named_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtensionAuditAppend.model_validate(
            append_payload(details={"nested": {"items": [{"refresh_token": "abc"}]}})
        )


def test_ordinary_detail_fields_are_accepted() -> None:
    append = ExtensionAuditAppend.model_validate(
        append_payload(details={"duration_ms": 12, "model": "gpt-5"})
    )
    assert append.details == {"duration_ms": 12, "model": "gpt-5"}


def test_too_many_detail_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtensionAuditAppend.model_validate(
            append_payload(details={f"field-{index}": index for index in range(65)})
        )


def test_an_oversized_detail_payload_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtensionAuditAppend.model_validate(append_payload(details={"blob": "x" * 17_000}))


@pytest.mark.parametrize("ref", ["run:abc", "artifact:file-9", "a" * 32 + ":x"])
def test_well_formed_evidence_references_are_accepted(ref: str) -> None:
    append = ExtensionAuditAppend.model_validate(append_payload(evidence_refs=[ref]))
    assert append.evidence_refs == [ref]


@pytest.mark.parametrize(
    "ref",
    ["missing-separator", ":no-kind", "no-identifier:", "1nvalid:identifier", "kind:" + "x" * 161],
)
def test_malformed_evidence_references_are_rejected(ref: str) -> None:
    with pytest.raises(ValidationError):
        ExtensionAuditAppend.model_validate(append_payload(evidence_refs=[ref]))


def test_too_many_evidence_references_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtensionAuditAppend.model_validate(
            append_payload(evidence_refs=[f"run:ref-{index}" for index in range(21)])
        )


def test_unknown_fields_are_rejected_by_the_boundary() -> None:
    with pytest.raises(ValidationError):
        ExtensionAuditAppend.model_validate(append_payload(actor_id="forged-admin"))
