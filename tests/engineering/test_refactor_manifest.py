"""Historical provenance resolves to current owners, never removed core business."""

from scripts.audit_refactor_manifest import acceptance, current_targets, reference_rows


def test_execution_acceptance_keeps_the_existing_test_directory():
    assert acceptance("src/opsmesh/orchestration/runs/service.py") == (
        "tests/orchestration;tests/execution"
    )


def test_historical_targets_resolve_across_structural_moves():
    assert current_targets("src/opsmesh/execution/runs/agents.py") == [
        "src/opsmesh/orchestration/execution/agents.py"
    ]
    assert current_targets("src/opsmesh/execution/sessions/temporal.py") == [
        "src/opsmesh/orchestration/execution/session.py"
    ]
    assert current_targets(
        "src/opsmesh/orchestration/definitions/capabilities/configuration.py"
    ) == ["src/opsmesh/orchestration/configurations/bindings/validation.py"]


def test_all_reference_destinations_exist_and_removed_business_is_explicit():
    rows = reference_rows()
    costs = [
        row for row in rows if row["planned_target"].startswith("src/opsmesh/governance/costs/")
    ]
    assert costs
    assert all(row["disposition"] == "delete_currency_cost_control" for row in costs)
    assert all(not row["final_targets"] for row in costs)
    mail = [row for row in rows if row["planned_target"].startswith("src/opsmesh/platform/mail/")]
    assert mail
    assert all(row["disposition"] == "external_unimplemented" for row in mail)
    assert all(
        row["external_reference"] == "extension-center:notification-delivery" for row in mail
    )
    assert all(not row["final_targets"] for row in mail)
    assert not any("src/opsmesh/execution/" in row["final_targets"] for row in rows)
