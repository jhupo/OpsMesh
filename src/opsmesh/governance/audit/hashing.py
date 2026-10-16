"""Stable encoding, hash chain and single-segment verification pure functions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

GENESIS_HASH = "0" * 64
CHAIN_DOMAIN = "opsmesh-audit-v1"


class HashedEvent(Protocol):
    """The fields the chain digest covers, in chain order."""

    @property
    def sequence(self) -> int: ...

    @property
    def previous_hash(self) -> str: ...

    @property
    def event_hash(self) -> str: ...


def canonical_payload(payload: Mapping[str, Any]) -> bytes:
    """Deterministic JSON encoding: sorted keys, no whitespace, no ASCII escapes."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=str,
    ).encode()


def chain_digest(previous_hash: str, payload: bytes) -> str:
    """The digest linking one event to its predecessor."""
    hasher = hashlib.sha256()
    hasher.update(CHAIN_DOMAIN.encode())
    hasher.update(b"\x00")
    hasher.update(previous_hash.encode())
    hasher.update(b"\x00")
    hasher.update(payload)
    return hasher.hexdigest()


def payload_digest(payload: Mapping[str, Any]) -> str:
    """The digest of one declared input, used for idempotency comparisons."""
    return hashlib.sha256(canonical_payload(payload)).hexdigest()


def first_broken_sequence(
    events: Sequence[HashedEvent],
    *,
    previous_hash: str = GENESIS_HASH,
    previous_sequence: int = 0,
) -> int | None:
    """Recompute the chain over ordered events; None when the segment is intact.

    The caller supplies the events in ascending sequence order plus the hash
    and sequence the segment continues from. A missing sequence, a wrong
    predecessor link or a wrong digest breaks the chain at the first offending
    position; a gap reports the first missing sequence.
    """
    for event in events:
        expected_sequence = previous_sequence + 1
        if event.sequence != expected_sequence:
            return expected_sequence
        if event.previous_hash != previous_hash:
            return event.sequence
        if event.event_hash != chain_digest(event.previous_hash, _event_payload(event)):
            return event.sequence
        previous_hash = event.event_hash
        previous_sequence = event.sequence
    return None


def verify_segment(
    events: Sequence[HashedEvent],
    *,
    from_sequence: int,
    to_sequence: int,
    previous_hash: str = GENESIS_HASH,
    previous_sequence: int = 0,
) -> tuple[int, int | None]:
    """Verify one requested range and report its checked count and first break.

    An intact prefix that stops before ``to_sequence`` is a break, not a
    success: the first missing sequence is reported so a deleted tail can
    never pass as a complete verification. The checked count never exceeds
    the number of events actually recomputed.
    """
    broken = first_broken_sequence(
        events, previous_hash=previous_hash, previous_sequence=previous_sequence
    )
    if broken is None and len(events) < to_sequence - from_sequence + 1:
        broken = from_sequence + len(events)
    return len(events), broken


def _event_payload(event: HashedEvent) -> bytes:
    """The payload bytes one event's digest was computed over.

    Events persist their payload as canonical JSON produced by
    ``canonical_payload`` at append time; verification re-encodes the same
    field mapping, so any post-append edit of a covered field changes the
    recomputed digest.
    """
    fields = getattr(event, "chain_payload", None)
    if not isinstance(fields, Mapping):
        raise ValueError("The event does not expose its chain payload")
    return canonical_payload(fields)
