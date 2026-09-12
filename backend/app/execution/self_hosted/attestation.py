"""Self-hosted capability attestation and trust-boundary helpers.

The control plane can verify the integrity and origin of a capability report when a
workspace configures the connector attestation secret.  That proof is intentionally
kept separate from host-isolation claims: a shared HMAC does not prove that a user's
machine is running a container, VM, or other sandbox.  Host isolation therefore stays
unverified unless a future platform attestation provider is added explicitly.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from backend.app.core.config import Settings

CAPABILITY_ATTESTATION_PROTOCOL = "opsmesh.self_hosted.attestation.v1"
CAPABILITY_ATTESTATION_MAX_AGE = timedelta(minutes=10)
CAPABILITY_ATTESTATION_FUTURE_SKEW = timedelta(minutes=1)
_ATTESTATION_MODES = frozenset(
    {"docker", "podman", "microvm", "vm", "sandbox", "process", "host", "unknown"}
)
_ATTESTATION_KEYS = frozenset(
    {
        "protocol",
        "workspace_id",
        "machine_id",
        "isolation_mode",
        "isolation_enforced",
        "capabilities_sha256",
        "issued_at",
        "nonce",
        "key_id",
        "signature",
    }
)
_SIGNED_ATTESTATION_KEYS = _ATTESTATION_KEYS - {"signature"}


@dataclass(frozen=True)
class CapabilityAttestationResult:
    state: str
    fingerprint: str | None
    metadata: dict[str, object]
    host_isolation_verified: bool
    attested_at: datetime | None


def evaluate_capability_attestation(
    *,
    workspace_id: UUID,
    machine_id: str,
    capabilities: Mapping[str, object],
    attestation: object,
    settings: Settings,
    now: datetime | None = None,
) -> CapabilityAttestationResult:
    """Verify a bounded, signed capability report without trusting host claims.

    Missing or invalid reports are accepted for normal self-hosted operation but are
    recorded as ``untrusted``.  Callers that need stronger guarantees must require the
    resulting state explicitly before assigning work.
    """

    current_time = now or datetime.now(UTC)
    if attestation is None:
        return _untrusted("attestation_missing")
    if not isinstance(attestation, Mapping):
        return _untrusted("attestation_not_an_object")
    unknown_keys = sorted(str(key) for key in attestation if key not in _ATTESTATION_KEYS)
    if unknown_keys:
        return _untrusted("attestation_contains_unsupported_fields")

    normalized = {key: attestation[key] for key in _SIGNED_ATTESTATION_KEYS if key in attestation}
    fingerprint = _report_fingerprint(workspace_id, machine_id, normalized)
    metadata = _safe_metadata(normalized, fingerprint)
    if normalized.get("protocol") != CAPABILITY_ATTESTATION_PROTOCOL:
        return _untrusted(
            "attestation_protocol_invalid",
            fingerprint=fingerprint,
            metadata=metadata,
        )
    if normalized.get("machine_id") != machine_id:
        return _untrusted(
            "attestation_machine_mismatch",
            fingerprint=fingerprint,
            metadata=metadata,
        )
    workspace_claim = normalized.get("workspace_id")
    if workspace_claim is not None and workspace_claim != str(workspace_id):
        return _untrusted(
            "attestation_workspace_mismatch",
            fingerprint=fingerprint,
            metadata=metadata,
        )

    isolation_mode = normalized.get("isolation_mode")
    if not isinstance(isolation_mode, str) or isolation_mode not in _ATTESTATION_MODES:
        return _untrusted(
            "attestation_isolation_mode_invalid",
            fingerprint=fingerprint,
            metadata=metadata,
        )
    isolation_enforced = normalized.get("isolation_enforced")
    if not isinstance(isolation_enforced, bool):
        return _untrusted(
            "attestation_isolation_claim_invalid",
            fingerprint=fingerprint,
            metadata=metadata,
        )
    capabilities_digest = normalized.get("capabilities_sha256")
    expected_capabilities_digest = capability_digest(capabilities)
    if capabilities_digest != expected_capabilities_digest:
        return _untrusted(
            "attestation_capabilities_mismatch",
            fingerprint=fingerprint,
            metadata=metadata,
        )

    issued_at = _parse_timestamp(normalized.get("issued_at"))
    if issued_at is None:
        return _untrusted(
            "attestation_timestamp_invalid",
            fingerprint=fingerprint,
            metadata=metadata,
        )
    if issued_at < current_time - CAPABILITY_ATTESTATION_MAX_AGE:
        return _untrusted("attestation_expired", fingerprint=fingerprint, metadata=metadata)
    if issued_at > current_time + CAPABILITY_ATTESTATION_FUTURE_SKEW:
        return _untrusted(
            "attestation_timestamp_in_future",
            fingerprint=fingerprint,
            metadata=metadata,
        )

    signature = attestation.get("signature")
    if not isinstance(signature, str) or len(signature) != 64:
        return _untrusted(
            "attestation_signature_invalid",
            fingerprint=fingerprint,
            metadata=metadata,
        )
    try:
        bytes.fromhex(signature)
    except ValueError:
        return _untrusted(
            "attestation_signature_invalid",
            fingerprint=fingerprint,
            metadata=metadata,
        )
    secret = (settings.self_hosted_attestation_secret or "").strip()
    if not secret:
        return _untrusted(
            "attestation_verifier_not_configured",
            fingerprint=fingerprint,
            metadata=metadata,
        )
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        _signed_material(workspace_id, machine_id, normalized).encode("utf-8"),
        sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature.lower(), expected_signature):
        return _untrusted(
            "attestation_signature_invalid",
            fingerprint=fingerprint,
            metadata=metadata,
        )

    metadata["verification_method"] = "hmac-sha256"
    metadata["host_isolation_verified"] = False
    return CapabilityAttestationResult(
        state="verified",
        fingerprint=fingerprint,
        metadata=metadata,
        host_isolation_verified=False,
        attested_at=issued_at,
    )


def capability_digest(capabilities: Mapping[str, object]) -> str:
    return f"sha256:{sha256(_canonical_json(dict(capabilities)).encode('utf-8')).hexdigest()}"


def attestation_signature(
    *,
    workspace_id: UUID,
    machine_id: str,
    attestation: Mapping[str, object],
    secret: str,
) -> str:
    """Create the connector-side signature for the documented report contract."""

    normalized = {key: attestation[key] for key in _SIGNED_ATTESTATION_KEYS if key in attestation}
    return hmac.new(
        secret.strip().encode("utf-8"),
        _signed_material(workspace_id, machine_id, normalized).encode("utf-8"),
        sha256,
    ).hexdigest()


def _untrusted(
    reason: str,
    *,
    fingerprint: str | None = None,
    metadata: dict[str, object] | None = None,
) -> CapabilityAttestationResult:
    result_metadata = dict(metadata or {})
    result_metadata["reason"] = reason
    result_metadata["host_isolation_verified"] = False
    return CapabilityAttestationResult(
        state="untrusted",
        fingerprint=fingerprint,
        metadata=result_metadata,
        host_isolation_verified=False,
        attested_at=None,
    )


def _report_fingerprint(
    workspace_id: UUID,
    machine_id: str,
    payload: Mapping[str, object],
) -> str:
    material = _signed_material(workspace_id, machine_id, payload)
    return f"sha256:{sha256(material.encode('utf-8')).hexdigest()}"


def _signed_material(
    workspace_id: UUID,
    machine_id: str,
    payload: Mapping[str, object],
) -> str:
    return f"{workspace_id}:{machine_id}:{_canonical_json(dict(payload))}"


def _canonical_json(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _safe_metadata(payload: Mapping[str, object], fingerprint: str) -> dict[str, object]:
    metadata: dict[str, object] = {
        "protocol": payload.get("protocol"),
        "workspace_id": payload.get("workspace_id"),
        "machine_id": payload.get("machine_id"),
        "isolation_mode": payload.get("isolation_mode"),
        "isolation_enforced": payload.get("isolation_enforced"),
        "capabilities_sha256": payload.get("capabilities_sha256"),
        "key_id": payload.get("key_id"),
        "issued_at": payload.get("issued_at"),
        "attestation_sha256": fingerprint,
    }
    nonce = payload.get("nonce")
    if isinstance(nonce, str) and nonce:
        metadata["nonce_sha256"] = f"sha256:{sha256(nonce.encode('utf-8')).hexdigest()}"
    return metadata


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
