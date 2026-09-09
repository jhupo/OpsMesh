import pytest

from backend.app.model_providers.health_probes import provider_health_probes


def test_provider_health_probes_preserve_order_and_deduplicate() -> None:
    assert provider_health_probes(None) == ("models", "inference")
    assert provider_health_probes(["inference", "models", "inference"]) == ("inference", "models")


@pytest.mark.parametrize("value", [[], "models", ["unknown"], ["models", 123], [""]])
def test_provider_health_probes_reject_malformed_input(value: object) -> None:
    with pytest.raises(ValueError):
        provider_health_probes(value)
