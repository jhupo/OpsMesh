"""Changes to shared fixtures and deleted tests must still exercise product owners."""

from scripts.ci_tests import select_tests


def test_root_fixture_change_runs_all_product_tests():
    targets = select_tests(["tests/conftest.py"])
    assert "tests/execution/test_runs_postgres.py" in targets
    assert "tests/channels/test_channels_postgres.py" in targets


def test_removed_test_and_fixture_changes_cover_remaining_feature_tests():
    for path in ("tests/execution/test_removed.py", "tests/execution/conftest.py"):
        assert "tests/execution/test_runs_postgres.py" in select_tests([path])


def test_identity_quota_change_covers_dependent_run_and_channel_boundaries():
    targets = select_tests(["src/opsmesh/identity/quotas/service.py"])
    assert "tests/execution/test_runs_postgres.py" in targets
    assert any(path.startswith("tests/channels/") for path in targets)


def test_orchestration_change_includes_native_execution_test_directory():
    targets = select_tests(["src/opsmesh/orchestration/execution/runner.py"])
    assert "tests/orchestration/test_flows_postgres.py" in targets
    assert "tests/execution/test_runs_postgres.py" in targets
    assert "tests/execution/test_sandbox_docker.py" in targets
    assert any(path.startswith("tests/channels/") for path in targets)


def test_platform_model_grants_cover_injected_execution_and_publication_boundaries():
    targets = select_tests(["src/opsmesh/platform/model_connections/service.py"])
    assert "tests/platform/test_model_connections_postgres.py" in targets
    assert "tests/orchestration/test_configurations_postgres.py" in targets
    assert "tests/execution/test_runs_postgres.py" in targets
    assert "tests/capabilities/test_publication_review_routes.py" in targets


def test_sdk_parameter_change_includes_authoritative_server_consumers():
    targets = select_tests(["sdk/python/src/opsmesh_sdk/composition/parameters.py"])
    assert "sdk/python/tests/test_parameters.py" in targets
    assert "tests/capabilities/test_capability_primitives.py" in targets
    assert "tests/orchestration/test_binding_parameters.py" in targets
    assert "tests/execution/test_runs_postgres.py" in targets
