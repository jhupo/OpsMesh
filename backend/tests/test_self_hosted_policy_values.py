import pytest

from backend.app.self_hosted.policy import positive_policy_int


@pytest.mark.parametrize("value", [None, True, False, 0, -1, "10", 1.5])
def test_invalid_positive_policy_values(value: object) -> None:
    assert positive_policy_int(value) is None


def test_positive_policy_value() -> None:
    assert positive_policy_int(10) == 10
