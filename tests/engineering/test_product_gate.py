"""Release acceptance cannot succeed from missing credentials or skipped tests."""

import xml.etree.ElementTree as ET

import pytest

from scripts.check_product_acceptance import (
    PRODUCT_TESTS,
    REQUIRED_ENV,
    main,
    require_complete_report,
)


def test_missing_model_configuration_refuses_to_launch(monkeypatch, capsys):
    for name in REQUIRED_ENV:
        monkeypatch.setenv(name, "configured")
    monkeypatch.delenv("OPSMESH_TEST_MODEL_API_KEY")
    assert main() == 1
    error = capsys.readouterr().err
    assert "OPSMESH_TEST_MODEL_API_KEY" in error
    assert "configured" not in error


@pytest.mark.parametrize("outcome", ["skipped", "failure", "error", "missing", "passed"])
def test_product_report_requires_all_scenarios_to_execute(tmp_path, outcome):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for selected in PRODUCT_TESTS:
        module = selected.split("::", 1)[0].removesuffix(".py").replace("/", ".")
        case = ET.SubElement(suite, "testcase", classname=module, name="scenario")
    if outcome == "missing":
        suite.remove(case)
    elif outcome != "passed":
        ET.SubElement(case, outcome)
    report = tmp_path / "products.xml"
    ET.ElementTree(root).write(report)
    if outcome == "passed":
        require_complete_report(report)
    else:
        with pytest.raises(ValueError):
            require_complete_report(report)
