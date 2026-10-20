"""Session contract tests without a database.

These cover the pure contract surface of a history operation: the
payload a fingerprint is taken over, the result shape a receipt stores
and replays, and the fail-closed run guard default.
"""

from __future__ import annotations

from uuid import UUID

from opsmesh.orchestration.sessions.contracts import (
    HistoryMutationResult,
    HistoryOperation,
    RejectingSessionRunActivity,
    operation_payload,
)
from opsmesh.shared.serialization import payload_digest


class TestOperationPayload:
    def test_a_read_carries_only_its_limit(self) -> None:
        payload = operation_payload(HistoryOperation(method="get_items", limit=25))
        assert payload == {"method": "get_items", "limit": 25}

    def test_an_append_carries_only_its_items(self) -> None:
        items = ({"role": "user", "content": "hi"},)
        payload = operation_payload(HistoryOperation(method="add_items", items=items))
        assert payload == {"method": "add_items", "items": [{"role": "user", "content": "hi"}]}

    def test_a_pop_and_a_clear_carry_nothing_but_their_method(self) -> None:
        assert operation_payload(HistoryOperation(method="pop_item")) == {"method": "pop_item"}
        assert operation_payload(HistoryOperation(method="clear_session")) == {
            "method": "clear_session"
        }

    def test_the_fingerprint_is_stable_and_content_bound(self) -> None:
        first = HistoryOperation(method="add_items", items=({"role": "user"},))
        second = HistoryOperation(method="add_items", items=({"role": "user"},))
        other = HistoryOperation(method="add_items", items=({"role": "assistant"},))
        assert payload_digest(operation_payload(first)) == payload_digest(operation_payload(second))
        assert payload_digest(operation_payload(first)) != payload_digest(operation_payload(other))


class TestHistoryMutationResult:
    def test_an_append_reports_its_count_only(self) -> None:
        result = HistoryMutationResult(appended=2)
        assert (result.appended, result.item, result.cleared) == (2, None, False)

    def test_a_pop_reports_its_item_only(self) -> None:
        result = HistoryMutationResult(item={"role": "user"})
        assert (result.appended, result.item, result.cleared) == (None, {"role": "user"}, False)

    def test_a_pop_of_an_empty_history_reports_no_item(self) -> None:
        result = HistoryMutationResult(item=None)
        assert (result.appended, result.item, result.cleared) == (None, None, False)

    def test_a_clear_reports_only_that_it_ran(self) -> None:
        result = HistoryMutationResult(cleared=True)
        assert (result.appended, result.item, result.cleared) == (None, None, True)


class TestRejectingSessionRunActivity:
    def test_the_default_guard_treats_every_session_as_active(self) -> None:
        guard = RejectingSessionRunActivity()
        assert guard.has_active_runs(None, session_id=UUID(int=1)) is True  # type: ignore[arg-type]
