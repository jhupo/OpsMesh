"""Require real product execution, including model I/O, without skipped acceptance tests."""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REQUIRED_ENV = (
    "OPSMESH_TEST_DATABASE_URL",
    "OPSMESH_TEST_REDIS_URL",
    "OPSMESH_TEST_TEMPORAL_ADDRESS",
    "OPSMESH_TEST_SANDBOX_IMAGE",
    "OPSMESH_TEST_EXTENSION_ARTIFACT",
    "OPSMESH_TEST_EXTENSION_BUILDER_IMAGE",
    "OPSMESH_TEST_SDK_WHEEL",
    "OPSMESH_TEST_MODEL_API_KEY",
    "OPSMESH_TEST_MODEL_BASE_URL",
    "OPSMESH_TEST_MODEL_NAME",
)
PRODUCT_TESTS = (
    "tests/execution/test_product_acceptance.py",
    "tests/execution/test_sandbox_docker.py",
    "tests/extensions/test_extension_product.py",
    "tests/extensions/test_managed_worker_product.py",
    "tests/execution/test_run_temporal.py::test_native_runner_history_codec_and_replay[real_model]",
    "tests/platform/test_operations_temporal.py",
    "tests/platform/test_sdk_http.py",
    "tests/temporal/test_payload_codec_replay.py",
)


def require_complete_report(report: Path) -> None:
    cases = list(ET.parse(report).getroot().iter("testcase"))
    if not cases or any(
        case.find(status) is not None
        for case in cases
        for status in ("skipped", "failure", "error")
    ):
        raise ValueError("Product acceptance requires executed tests with no skips or failures")
    # Each selected module/node must contribute evidence; an empty module is not acceptance.
    for selected in PRODUCT_TESTS:
        module = selected.split("::", 1)[0].removesuffix(".py").replace("/", ".")
        if not any(case.get("classname", "").startswith(module) for case in cases):
            raise ValueError(f"Missing product acceptance evidence: {module}")


def main() -> int:
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name, "").strip()]
    if missing:
        print("Product acceptance configuration missing: " + ", ".join(missing), file=sys.stderr)
        return 1
    if not Path(os.environ["OPSMESH_TEST_SDK_WHEEL"]).is_file():
        print("Product acceptance requires the built SDK wheel", file=sys.stderr)
        return 1
    environment = {
        **os.environ,
        "OPSMESH_TEST_PRODUCT": "1",
        "OPSMESH_TEST_EXTENSIONS_PRODUCT": "1",
        "OPSMESH_TEST_S9_S10_PRODUCT": "1",
        "OPSMESH_TEST_MANAGED_PRODUCT": "1",
        "OPSMESH_TEST_TEMPORAL_LOCAL": "1",
    }
    report = Path("reports/v4-products.xml")
    report.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *PRODUCT_TESTS,
            "-q",
            "-o",
            "addopts=",
            "--tb=short",
            "--basetemp=.tmp/product-acceptance",
            f"--junitxml={report}",
        ],
        env=environment,
        check=False,
    )
    if result.returncode:
        return result.returncode
    require_complete_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
