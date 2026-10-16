"""Audit hash chain pure-function tests: encoding, digests and segment verification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from opsmesh.governance.audit.hashing import (
    GENESIS_HASH,
    canonical_payload,
    chain_digest,
    first_broken_sequence,
    payload_digest,
    verify_segment,
)


@dataclass(frozen=True)
class FakeEvent:
    """The chain-visible shape of one audit event."""

    sequence: int
    previous_hash: str
    event_hash: str
    payload: dict[str, Any]

    @property
    def chain_payload(self) -> dict[str, Any]:
        return self.payload


def make_event(sequence: int, previous_hash: str, **overrides: Any) -> FakeEvent:
    payload: dict[str, Any] = {
        "sequence": sequence,
        "occurred_at": "2026-10-16T00:00:00+00:00",
        "actor_type": "platform",
        "actor_id": None,
        "action": "user.created",
        "target_type": "user",
        "target_id": "user-1",
        "summary": "Account opened",
    }
    payload.update(overrides)
    event_hash = chain_digest(previous_hash, canonical_payload(payload))
    return FakeEvent(
        sequence=sequence, previous_hash=previous_hash, event_hash=event_hash, payload=payload
    )


def build_chain(length: int) -> list[FakeEvent]:
    events: list[FakeEvent] = []
    previous_hash = GENESIS_HASH
    for sequence in range(1, length + 1):
        event = make_event(sequence, previous_hash)
        events.append(event)
        previous_hash = event.event_hash
    return events


def test_canonical_payload_is_order_independent_and_compact() -> None:
    first = canonical_payload({"b": 1, "a": {"y": 2, "x": 3}})
    second = canonical_payload({"a": {"x": 3, "y": 2}, "b": 1})
    assert first == second
    assert first == b'{"a":{"x":3,"y":2},"b":1}'


def test_chain_digest_is_stable_and_domain_separated() -> None:
    payload = canonical_payload({"sequence": 1})
    first = chain_digest(GENESIS_HASH, payload)
    assert first == chain_digest(GENESIS_HASH, payload)
    assert first != chain_digest("1" * 64, payload)


def test_an_intact_chain_verifies() -> None:
    assert first_broken_sequence(build_chain(5)) is None


def test_a_tampered_field_breaks_the_chain_at_its_event() -> None:
    events = build_chain(3)
    events[1] = FakeEvent(
        sequence=events[1].sequence,
        previous_hash=events[1].previous_hash,
        event_hash=events[1].event_hash,
        payload={**events[1].payload, "summary": "Rewritten after append"},
    )
    assert first_broken_sequence(events) == 2


def test_a_reordered_link_breaks_the_chain() -> None:
    events = build_chain(3)
    events[2] = FakeEvent(
        sequence=events[2].sequence,
        previous_hash=events[0].event_hash,
        event_hash=events[2].event_hash,
        payload=events[2].payload,
    )
    assert first_broken_sequence(events) == 3


def test_a_missing_sequence_reports_the_first_gap() -> None:
    events = build_chain(4)
    del events[2]
    assert first_broken_sequence(events) == 3


def test_a_segment_continues_from_its_anchor() -> None:
    events = build_chain(4)
    anchor = events[1]
    segment = events[2:]
    assert (
        first_broken_sequence(
            segment, previous_hash=anchor.event_hash, previous_sequence=anchor.sequence
        )
        is None
    )
    # The right sequence anchor with the wrong hash breaks at the first event.
    wrong_hash = first_broken_sequence(
        segment, previous_hash=GENESIS_HASH, previous_sequence=anchor.sequence
    )
    assert wrong_hash == 3
    # The right hash with the wrong sequence anchor reports the first gap.
    assert (
        first_broken_sequence(segment, previous_hash=anchor.event_hash, previous_sequence=0) == 1
    )


def test_an_empty_segment_is_intact() -> None:
    assert first_broken_sequence([]) is None


def test_a_complete_requested_range_verifies() -> None:
    events = build_chain(3)
    assert verify_segment(events, from_sequence=1, to_sequence=3) == (3, None)


def test_a_deleted_tail_breaks_the_segment_at_the_first_missing_sequence() -> None:
    """An intact prefix shorter than the requested range is never a success."""
    events = build_chain(3)
    checked, broken = verify_segment(events[:1], from_sequence=1, to_sequence=3)
    assert checked == 1
    assert broken == 2
    # An empty first batch breaks at the first requested sequence.
    assert verify_segment([], from_sequence=1, to_sequence=3) == (0, 1)


def test_a_missing_tail_after_an_anchor_breaks_at_the_first_missing_sequence() -> None:
    events = build_chain(5)
    anchor = events[2]
    checked, broken = verify_segment(
        events[3:4],
        from_sequence=4,
        to_sequence=5,
        previous_hash=anchor.event_hash,
        previous_sequence=anchor.sequence,
    )
    assert checked == 1
    assert broken == 5


def test_payload_digest_distinguishes_inputs() -> None:
    assert payload_digest({"amount": 10}) == payload_digest({"amount": 10})
    assert payload_digest({"amount": 10}) != payload_digest({"amount": 11})


def test_the_event_payload_covers_occurred_at() -> None:
    occurred = datetime(2026, 10, 16, tzinfo=UTC)
    event = make_event(1, GENESIS_HASH, occurred_at=occurred.isoformat())
    assert event.payload["occurred_at"] == "2026-10-16T00:00:00+00:00"
    assert event.event_hash == chain_digest(GENESIS_HASH, canonical_payload(event.payload))
