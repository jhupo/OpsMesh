import pytest
from opsmesh_sdk.messages import MessageFilterResult
from pydantic import ValidationError


def test_rejection_input_is_bounded_and_only_valid_for_rejected_messages():
    result = MessageFilterResult(action="drop", rejection_input="Generate a brief rejection.")
    assert MessageFilterResult.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError, match="Allowed messages"):
        MessageFilterResult(action="allow", rejection_input="Do not send this")
    with pytest.raises(ValidationError):
        MessageFilterResult(action="drop", rejection_input="x" * 4001)
