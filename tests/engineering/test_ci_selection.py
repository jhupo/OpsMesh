"""Changes to shared fixtures and deleted tests must still exercise product owners."""

from scripts.ci_tests import select_tests


def test_root_fixture_change_runs_all_product_tests():
    targets = select_tests(["tests/conftest.py"])
    assert "tests/execution/test_native_model.py" in targets
    assert "tests/users/test_quotas_postgres.py" in targets


def test_removed_test_and_fixture_changes_cover_remaining_feature_tests():
    for path in ("tests/execution/test_removed.py", "tests/execution/conftest.py"):
        assert "tests/execution/test_runtime_identity.py" in select_tests([path])


def test_user_quota_change_covers_dependent_execution_and_worker_boundaries():
    targets = select_tests(["src/opsmesh/users/quotas/service.py"])
    assert "tests/execution/test_native_model.py" in targets
    assert "tests/bootstrap/test_worker_lifecycle.py" in targets


def test_agent_change_includes_native_execution_test_directory():
    targets = select_tests(["src/opsmesh/agents/assembly.py"])
    assert "tests/execution/test_native_model.py" in targets
    assert "tests/execution/test_sandbox_docker.py" in targets
    assert not any(path.startswith(("tests/channels/", "tests/extensions/")) for path in targets)


def test_plugin_change_covers_its_execution_consumers():
    targets = select_tests(["src/opsmesh/plugins/manager.py"])
    assert "tests/execution/test_runtime_identity.py" in targets
    assert "tests/bootstrap/test_api_assembly.py" in targets


def test_plugin_sdk_change_includes_host_and_installation_consumers():
    targets = select_tests(["sdk/python/src/opsmesh_sdk/context.py"])
    assert "sdk/python/tests/test_extension_sdk_plugins.py" in targets
    assert "tests/bootstrap/test_api_assembly.py" in targets
    assert "deploy/cli/tests/test_plugin_installation.py" in targets
